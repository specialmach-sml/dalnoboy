require("dotenv").config({ path: "../.env" });

const BOT_TOKEN = process.env.BOT_TOKEN;

const express = require("express");
const http = require("http");
const cors = require("cors");
const { Pool } = require("pg");
const crypto = require("crypto");
const { Server } = require("socket.io");

const app = express();
const server = http.createServer(app);
const io = new Server(server, {
  cors: { origin: "*" },

  // === TELEGRAM_SOCKET_ALLOW_REQUEST_V1 ===
  allowRequest: (req, callback) => {
    try {
      const url = new URL(req.url || "", "http://localhost");
      const initData = url.searchParams.get("initData") || "";
      const verified = verifyTelegramInitData(initData);
      return callback(null, !!verified.ok);
    } catch (_) {
      return callback(null, false);
    }
  }
});
app.use(cors());
app.use(express.json());

// === TELEGRAM_MAP_API_AUTH_V1 ===
function verifyTelegramInitData(initData) {
  try {
    if (!BOT_TOKEN || !initData || typeof initData !== "string") {
      return { ok: false };
    }

    const params = new URLSearchParams(initData);
    const hash = params.get("hash");
    if (!hash) return { ok: false };

    params.delete("hash");

    const dataCheckString = Array.from(params.entries())
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([k, v]) => `${k}=${v}`)
      .join("\n");

    const secretKey = crypto
      .createHmac("sha256", "WebAppData")
      .update(BOT_TOKEN)
      .digest();

    const calculatedHash = crypto
      .createHmac("sha256", secretKey)
      .update(dataCheckString)
      .digest("hex");

    const a = Buffer.from(hash, "hex");
    const b = Buffer.from(calculatedHash, "hex");

    if (a.length !== b.length || !crypto.timingSafeEqual(a, b)) {
      return { ok: false };
    }

    const authDate = Number(params.get("auth_date") || 0);
    const ageSec = Math.floor(Date.now() / 1000) - authDate;
    if (!authDate || ageSec < -300 || ageSec > 604800) {
      return { ok: false };
    }

    let user = null;
    try {
      const rawUser = params.get("user");
      if (rawUser) user = JSON.parse(rawUser);
    } catch (_) {}

    return { ok: true, user };
  } catch (_) {
    return { ok: false };
  }
}

function requireTelegramWebApp(req, res, next) {
  const initData = req.get("x-telegram-init-data") || "";
  const verified = verifyTelegramInitData(initData);

  if (!verified.ok) {
    return res.status(401).json({
      success: false,
      error: "telegram_webapp_auth_required"
    });
  }

  const signedId = verified.user && verified.user.id ? String(verified.user.id) : "";

  if (!signedId) {
    return res.status(403).json({
      success: false,
      error: "signed_telegram_user_required"
    });
  }

  const claimedId = String(
    (req.query && req.query.telegram_id) ||
    (req.body && req.body.telegram_id) ||
    ""
  );

  if (claimedId && signedId && claimedId !== signedId) {
    return res.status(403).json({
      success: false,
      error: "telegram_id_mismatch"
    });
  }

  req.telegramWebAppUser = verified.user || null;
  return next();
}

const protectedMapApiPaths = new Set([
  "/api/cargo/open",
  "/api/trucks/active",
  "/api/nearby",
  "/api/route-addons",
  "/api/truck/location",
  "/api/me/truck",
  "/api/cargo/create"
]);

app.use((req, res, next) => {
  if (req.method === "OPTIONS") return next();
  const normalizedPath = (req.path.replace(/\/+$/, "") || "/").toLowerCase();
  if (!protectedMapApiPaths.has(normalizedPath)) return next();
  return requireTelegramWebApp(req, res, next);
});
// === END TELEGRAM_MAP_API_AUTH_V1 ===


const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
  ssl: {
    rejectUnauthorized: false,
  },
});

async function initDb() {
  await pool.query(`
    CREATE TABLE IF NOT EXISTS orders (
      id SERIAL PRIMARY KEY,
      from_city TEXT NOT NULL,
      to_city TEXT NOT NULL,
      cargo TEXT NOT NULL,
      weight INTEGER NOT NULL,
      price INTEGER NOT NULL,
      created_at TIMESTAMP DEFAULT NOW()
    )
  `);

  console.log("Postgres connected");
}

initDb();

app.get("/orders", async (req, res) => {
  const result = await pool.query(
    "SELECT * FROM orders ORDER BY id DESC"
  );
  res.json(result.rows);
});

app.post("/orders", async (req, res) => {
  const { from, to, cargo, weight, price } = req.body;

  const result = await pool.query(
    `
    INSERT INTO orders
    (from_city, to_city, cargo, weight, price)
    VALUES ($1,$2,$3,$4,$5)
    RETURNING *
    `,
    [from, to, cargo, weight, price]
  );

  res.json(result.rows[0]);
});

app.delete("/orders/:id", async (req, res) => {
  await pool.query(
    "DELETE FROM orders WHERE id=$1",
    [req.params.id]
  );

  res.json({ success: true });
});





app.post("/api/app/login", async (req, res) => {
  const client = await pool.connect();

  try {
    const { code } = req.body || {};

    if (!code || String(code).trim().length < 4) {
      return res.status(400).json({
        success: false,
        error: "code required"
      });
    }

    await client.query("BEGIN");

    const codeResult = await client.query(
      `
      SELECT
        c.id AS code_id,
        c.user_id,
        c.telegram_id,
        c.expires_at,
        c.used_at,
        u.full_name,
        u.role,
        u.plan_type,
        u.verified,
        u.banned
      FROM app_login_codes c
      JOIN users u ON u.id = c.user_id
      WHERE c.code = $1
      LIMIT 1
      `,
      [String(code).trim()]
    );

    if (!codeResult.rows.length) {
      await client.query("ROLLBACK");
      return res.status(401).json({
        success: false,
        error: "invalid code"
      });
    }

    const row = codeResult.rows[0];

    if (row.used_at) {
      await client.query("ROLLBACK");
      return res.status(401).json({
        success: false,
        error: "code already used"
      });
    }

    if (new Date(row.expires_at).getTime() < Date.now()) {
      await client.query("ROLLBACK");
      return res.status(401).json({
        success: false,
        error: "code expired"
      });
    }

    if (row.banned) {
      await client.query("ROLLBACK");
      return res.status(403).json({
        success: false,
        error: "user banned"
      });
    }

    const consentResult = await client.query(
      `
      SELECT COUNT(*)::int AS cnt
      FROM user_consents
      WHERE user_id = $1
        AND revoked_at IS NULL
        AND consent_type IN (
          'user_agreement',
          'privacy_policy',
          'personal_data_consent',
          'geo_consent'
        )
      `,
      [row.user_id]
    );

    if (consentResult.rows[0].cnt < 4) {
      await client.query("ROLLBACK");
      return res.status(403).json({
        success: false,
        error: "legal consent required"
      });
    }

    const token = crypto.randomBytes(32).toString("hex");
    const tokenHash = crypto
      .createHash("sha256")
      .update(token)
      .digest("hex");

    await client.query(
      `
      INSERT INTO app_sessions (
        user_id,
        telegram_id,
        token_hash,
        user_agent,
        ip_address,
        expires_at
      )
      VALUES ($1, $2, $3, $4, $5, now() + interval '30 days')
      `,
      [
        row.user_id,
        row.telegram_id,
        tokenHash,
        req.headers["user-agent"] || null,
        req.ip || null
      ]
    );

    await client.query(
      `
      UPDATE app_login_codes
      SET used_at = now()
      WHERE id = $1
      `,
      [row.code_id]
    );

    await client.query(
      `
      INSERT INTO audit_log (user_id, action, payload)
      VALUES ($1, 'app_login_success', $2::jsonb)
      `,
      [
        row.user_id,
        JSON.stringify({
          source: "api",
          user_agent: req.headers["user-agent"] || null
        })
      ]
    );

    await client.query("COMMIT");

    return res.json({
      success: true,
      token,
      user: {
        id: row.user_id,
        telegram_id: row.telegram_id,
        full_name: row.full_name,
        role: row.role,
        plan_type: row.plan_type,
        verified: row.verified
      }
    });
  } catch (err) {
    await client.query("ROLLBACK");
    console.error("app login error", err);

    return res.status(500).json({
      success: false,
      error: "server error"
    });
  } finally {
    client.release();
  }
});




app.get("/api/app/me", async (req, res) => {
  try {
    const auth = req.headers.authorization || "";

    if (!auth.startsWith("Bearer ")) {
      return res.status(401).json({
        success: false,
        error: "token required"
      });
    }

    const token = auth.slice(7).trim();

    if (!token) {
      return res.status(401).json({
        success: false,
        error: "token required"
      });
    }

    const tokenHash = crypto
      .createHash("sha256")
      .update(token)
      .digest("hex");

    const result = await pool.query(
      `
      SELECT
        s.id AS session_id,
        s.user_id,
        s.telegram_id,
        s.expires_at,
        u.full_name,
        u.role,
        u.plan_type,
        u.verified,
        u.banned
      FROM app_sessions s
      JOIN users u ON u.id = s.user_id
      WHERE s.token_hash = $1
        AND s.revoked_at IS NULL
        AND s.expires_at > now()
      LIMIT 1
      `,
      [tokenHash]
    );

    if (!result.rows.length) {
      return res.status(401).json({
        success: false,
        error: "invalid session"
      });
    }

    const row = result.rows[0];

    if (row.banned) {
      return res.status(403).json({
        success: false,
        error: "user banned"
      });
    }

    return res.json({
      success: true,
      user: {
        id: row.user_id,
        telegram_id: row.telegram_id,
        full_name: row.full_name,
        role: row.role,
        plan_type: row.plan_type,
        verified: row.verified
      },
      session: {
        id: row.session_id,
        expires_at: row.expires_at
      }
    });
  } catch (err) {
    console.error("app me error", err);

    return res.status(500).json({
      success: false,
      error: "server error"
    });
  }
});




app.get("/api/app/my-cargo", async (req, res) => {
  try {
    const auth = req.headers.authorization || "";

    if (!auth.startsWith("Bearer ")) {
      return res.status(401).json({ success: false, error: "token required" });
    }

    const token = auth.slice(7).trim();

    if (!token) {
      return res.status(401).json({ success: false, error: "token required" });
    }

    const tokenHash = crypto.createHash("sha256").update(token).digest("hex");

    const sessionResult = await pool.query(
      `
      SELECT s.user_id, u.role, u.plan_type, u.banned
      FROM app_sessions s
      JOIN users u ON u.id = s.user_id
      WHERE s.token_hash = $1
        AND s.revoked_at IS NULL
        AND s.expires_at > now()
      LIMIT 1
      `,
      [tokenHash]
    );

    if (!sessionResult.rows.length) {
      return res.status(401).json({ success: false, error: "invalid session" });
    }

    const user = sessionResult.rows[0];

    if (user.banned) {
      return res.status(403).json({ success: false, error: "user banned" });
    }

    const cargoResult = await pool.query(
      `
      SELECT
        id,
        from_city,
        to_city,
        description,
        status,
        ROUND(price_amount::numeric, 0)::text AS price_amount,
        price_currency,
        CASE WHEN distance_km IS NULL THEN NULL ELSE ROUND(distance_km::numeric, 1)::text END AS distance_km,
        CASE WHEN rate_per_km IS NULL THEN NULL ELSE ROUND(rate_per_km::numeric, 2)::text END AS rate_per_km,
        cargo_type,
        created_at
      FROM cargo
      WHERE created_by = $1
      ORDER BY id DESC
      LIMIT 100
      `,
      [user.user_id]
    );

    return res.json({
      success: true,
      cargo: cargoResult.rows
    });
  } catch (err) {
    console.error("app my-cargo error", err);
    return res.status(500).json({ success: false, error: "server error" });
  }
});



async function getAppUserFromReq(req) {
  const auth = req.headers.authorization || "";
  if (!auth.startsWith("Bearer ")) return null;

  const tokenHash = crypto.createHash("sha256").update(auth.slice(7).trim()).digest("hex");

  const r = await pool.query(`
    SELECT s.user_id
    FROM app_sessions s
    JOIN users u ON u.id = s.user_id
    WHERE s.token_hash = $1
      AND s.revoked_at IS NULL
      AND s.expires_at > now()
      AND COALESCE(u.banned,false) = false
    LIMIT 1
  `, [tokenHash]);

  return r.rows[0] || null;
}


app.post("/api/app/bind-phone", async (req, res) => {
  try {
    const user = await getAppUserFromReq(req);

    if (!user) {
      return res.status(401).json({
        success: false,
        error: "session invalid"
      });
    }

    const rawPhone = String((req.body && req.body.phone) || "").trim();
    const digits = rawPhone.replace(/\D/g, "");

    if (digits.length < 10 || digits.length > 15) {
      return res.status(400).json({
        success: false,
        error: "invalid phone"
      });
    }

    let phone = "+" + digits;

    if (digits.length === 10) {
      phone = "+7" + digits;
    }

    if (digits.length === 11 && digits[0] === "8") {
      phone = "+7" + digits.slice(1);
    }

    if (digits.length === 11 && digits[0] === "7") {
      phone = "+7" + digits.slice(1);
    }

    const exists = await pool.query(
      `
      SELECT id
      FROM users
      WHERE phone = $1
        AND id <> $2
      LIMIT 1
      `,
      [phone, user.user_id]
    );

    if (exists.rows.length) {
      return res.status(409).json({
        success: false,
        error: "phone already used"
      });
    }

    await pool.query(
      `
      UPDATE users
      SET phone = $1,
          phone_verified = false,
          phone_verified_at = NULL
      WHERE id = $2
      `,
      [phone, user.user_id]
    );

    await pool.query(
      `
      INSERT INTO audit_log (user_id, action, payload)
      VALUES ($1, 'phone_bound', $2::jsonb)
      `,
      [
        user.user_id,
        JSON.stringify({
          source: "cabinet",
          phone
        })
      ]
    );

    return res.json({
      success: true,
      phone,
      phone_verified: false
    });
  } catch (err) {
    console.error("bind phone error", err);
    return res.status(500).json({
      success: false,
      error: "server error"
    });
  }
});




app.post("/api/app/request-phone-code", async (req, res) => {
  try {
    const user = await getAppUserFromReq(req);

    if (!user) {
      return res.status(401).json({ success: false, error: "session invalid" });
    }

    const userResult = await pool.query(
      `
      SELECT id, telegram_id, phone, phone_verified
      FROM users
      WHERE id = $1
      LIMIT 1
      `,
      [user.user_id]
    );

    if (!userResult.rows.length) {
      return res.status(404).json({ success: false, error: "user not found" });
    }

    const u = userResult.rows[0];

    if (!u.phone) {
      return res.status(400).json({ success: false, error: "phone not saved" });
    }

    if (!u.telegram_id) {
      return res.status(400).json({ success: false, error: "telegram not linked" });
    }

    if (!BOT_TOKEN) {
      return res.status(500).json({ success: false, error: "bot token missing" });
    }

    const recent = await pool.query(
      `
      SELECT id
      FROM phone_verification_codes
      WHERE user_id = $1
        AND used_at IS NULL
        AND created_at > now() - interval '1 minute'
      LIMIT 1
      `,
      [u.id]
    );

    if (recent.rows.length) {
      return res.status(429).json({
        success: false,
        error: "wait before requesting new code"
      });
    }

    await pool.query(
      `
      UPDATE phone_verification_codes
      SET used_at = now()
      WHERE user_id = $1
        AND used_at IS NULL
      `,
      [u.id]
    );

    const code = String(Math.floor(100000 + Math.random() * 900000));
    const codeHash = crypto
      .createHash("sha256")
      .update(code + ":" + u.id + ":" + u.phone)
      .digest("hex");

    await pool.query(
      `
      INSERT INTO phone_verification_codes (
        user_id,
        phone,
        code_hash,
        expires_at
      )
      VALUES ($1, $2, $3, now() + interval '10 minutes')
      `,
      [u.id, u.phone, codeHash]
    );

    const tgText =
      "📱 Код подтверждения телефона в Дальнобой: " + code +
      "\n\nКод действует 10 минут. Никому его не сообщайте.";

    const tgResponse = await fetch(`https://api.telegram.org/bot${BOT_TOKEN}/sendMessage`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        chat_id: u.telegram_id,
        text: tgText
      })
    });

    const tgData = await tgResponse.json().catch(() => ({}));

    if (!tgResponse.ok || tgData.ok === false) {
      console.error("telegram phone code send error", tgData);
      return res.status(500).json({
        success: false,
        error: "telegram send failed"
      });
    }

    await pool.query(
      `
      INSERT INTO audit_log (user_id, action, payload)
      VALUES ($1, 'phone_code_requested', $2::jsonb)
      `,
      [
        u.id,
        JSON.stringify({
          source: "cabinet",
          phone: u.phone
        })
      ]
    );

    return res.json({
      success: true,
      message: "code sent"
    });
  } catch (err) {
    console.error("request phone code error", err);
    return res.status(500).json({ success: false, error: "server error" });
  }
});




app.post("/api/app/verify-phone-code", async (req, res) => {
  try {
    const user = await getAppUserFromReq(req);

    if (!user) {
      return res.status(401).json({ success: false, error: "session invalid" });
    }

    const rawCode = String((req.body && req.body.code) || "").trim();
    const code = rawCode.replace(/\D/g, "");

    if (code.length !== 6) {
      return res.status(400).json({ success: false, error: "invalid code" });
    }

    const userResult = await pool.query(
      `
      SELECT id, phone
      FROM users
      WHERE id = $1
      LIMIT 1
      `,
      [user.user_id]
    );

    if (!userResult.rows.length || !userResult.rows[0].phone) {
      return res.status(400).json({ success: false, error: "phone not saved" });
    }

    const u = userResult.rows[0];

    const codeResult = await pool.query(
      `
      SELECT id, code_hash, attempts
      FROM phone_verification_codes
      WHERE user_id = $1
        AND phone = $2
        AND used_at IS NULL
        AND expires_at > now()
      ORDER BY id DESC
      LIMIT 1
      `,
      [u.id, u.phone]
    );

    if (!codeResult.rows.length) {
      return res.status(400).json({ success: false, error: "code expired or missing" });
    }

    const row = codeResult.rows[0];

    if (Number(row.attempts || 0) >= 5) {
      await pool.query(
        "UPDATE phone_verification_codes SET used_at = now() WHERE id = $1",
        [row.id]
      );

      return res.status(429).json({
        success: false,
        error: "too many attempts"
      });
    }

    const checkHash = crypto
      .createHash("sha256")
      .update(code + ":" + u.id + ":" + u.phone)
      .digest("hex");

    if (checkHash !== row.code_hash) {
      await pool.query(
        "UPDATE phone_verification_codes SET attempts = attempts + 1 WHERE id = $1",
        [row.id]
      );

      return res.status(400).json({
        success: false,
        error: "wrong code"
      });
    }

    await pool.query(
      `
      UPDATE phone_verification_codes
      SET used_at = now()
      WHERE id = $1
      `,
      [row.id]
    );

    await pool.query(
      `
      UPDATE users
      SET phone_verified = true,
          phone_verified_at = now()
      WHERE id = $1
      `,
      [u.id]
    );

    await pool.query(
      `
      INSERT INTO audit_log (user_id, action, payload)
      VALUES ($1, 'phone_verified', $2::jsonb)
      `,
      [
        u.id,
        JSON.stringify({
          source: "cabinet",
          phone: u.phone
        })
      ]
    );

    return res.json({
      success: true,
      phone_verified: true
    });
  } catch (err) {
    console.error("verify phone code error", err);
    return res.status(500).json({ success: false, error: "server error" });
  }
});



app.get("/api/app/responses", async (req, res) => {
  try {
    const user = await getAppUserFromReq(req);
    if (!user) return res.status(401).json({success:false,error:"session invalid"});

    const ownerRows = await pool.query(`
      SELECT
        r.id,
        r.status,
        r.message,
        r.created_at,
        c.id AS cargo_id,
        c.from_city,
        c.to_city,
        c.price_amount,
        c.price_currency,
        c.weight_kg,
        c.volume_m3,
        c.places_count,
        c.distance_km,
        c.rate_per_km,
        u.id AS driver_id,
        u.full_name AS driver_name,
        u.verified AS driver_verified,
        t.id AS truck_id,
        t.current_city,
        t.body_type,
        d.id AS deal_id
      FROM responses r
      JOIN cargo c ON c.id = r.cargo_id
      JOIN users u ON u.id = r.driver_id
      JOIN trucks t ON t.id = r.truck_id
      LEFT JOIN deals d ON d.response_id = r.id
      WHERE c.created_by = $1
      ORDER BY r.id DESC
      LIMIT 50
    `, [user.user_id]);

    const driverRows = await pool.query(`
      SELECT
        r.id,
        r.status,
        r.message,
        r.created_at,
        c.id AS cargo_id,
        c.from_city,
        c.to_city,
        c.price_amount,
        c.price_currency,
        c.weight_kg,
        c.volume_m3,
        c.places_count,
        c.distance_km,
        c.rate_per_km,
        c.created_by AS cargo_owner_id,
        owner.full_name AS owner_name,
        t.id AS truck_id,
        t.current_city,
        t.body_type,
        d.id AS deal_id
      FROM responses r
      JOIN cargo c ON c.id = r.cargo_id
      LEFT JOIN users owner ON owner.id = c.created_by
      LEFT JOIN trucks t ON t.id = r.truck_id
      LEFT JOIN deals d ON d.response_id = r.id
      WHERE r.driver_id = $1
      ORDER BY r.id DESC
      LIMIT 50
    `, [user.user_id]);

    return res.json({
      success: true,
      owner: ownerRows.rows,
      driver: driverRows.rows
    });
  } catch (err) {
    console.error("app responses list error", err);
    return res.status(500).json({success:false,error:"server error"});
  }
});


app.post("/api/app/response-action", async (req, res) => {
  const client = await pool.connect();

  try {
    const user = await getAppUserFromReq(req);
    if (!user) return res.status(401).json({success:false,error:"session invalid"});

    const b = req.body || {};
    const responseId = parseInt(b.response_id, 10);
    const action = String(b.action || "");

    if (!responseId || !["accept","reject"].includes(action)) {
      return res.status(400).json({success:false,error:"bad request"});
    }

    await client.query("BEGIN");

    const responseRes = await client.query(`
      SELECT
        r.id,
        r.status,
        r.cargo_id,
        r.truck_id,
        r.driver_id,
        driver.telegram_id AS driver_telegram_id,
        driver.full_name AS driver_name,
        c.created_by AS cargo_owner_id,
        c.from_city,
        c.to_city,
        c.price_amount,
        c.price_currency,
        c.weight_kg,
        c.volume_m3,
        c.places_count,
        c.rate_per_km,
        d.id AS deal_id
      FROM responses r
      JOIN cargo c ON c.id = r.cargo_id
      JOIN users driver ON driver.id = r.driver_id
      LEFT JOIN deals d ON d.response_id = r.id
      WHERE r.id = $1
      FOR UPDATE OF r, c
      `, [responseId]);

    if (!responseRes.rows.length) {
      await client.query("ROLLBACK");
      return res.status(404).json({success:false,error:"response not found"});
    }

    const row = responseRes.rows[0];

    if (String(row.cargo_owner_id) !== String(user.user_id)) {
      await client.query("ROLLBACK");
      return res.status(403).json({success:false,error:"not cargo owner"});
    }

    if (action === "reject") {
      await client.query(`
        UPDATE responses
        SET status = 'rejected'
        WHERE id = $1
      `, [responseId]);

      await client.query(`
        INSERT INTO audit_log (user_id, cargo_id, action, payload)
        VALUES ($1, $2, 'response_rejected', $3::jsonb)
      `, [
        user.user_id,
        row.cargo_id,
        JSON.stringify({response_id: responseId, source: "web_cabinet"})
      ]);

      await client.query("COMMIT");

      if (row.driver_telegram_id && BOT_TOKEN) {
        try {
          await fetch(`https://api.telegram.org/bot${BOT_TOKEN}/sendMessage`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              chat_id: row.driver_telegram_id,
              text:
                `❌ Ваш отклик отклонён\n\n` +
                `📦 Груз #${row.cargo_id}\n` +
                `🚩 ${row.from_city} → ${row.to_city}`
            })
          });
        } catch(e) {
          console.error("telegram reject response notify error", e);
        }
      }

      io.emit("response_status_updated", {
        response_id: responseId,
        status: "rejected",
        cargo_id: row.cargo_id,
        truck_id: row.truck_id,
        deal_id: null,
        updated_at: new Date().toISOString()
      });

      return res.json({success:true,response_id:responseId,status:"rejected"});
    }

    await client.query(`
      UPDATE responses
      SET status = 'accepted'
      WHERE id = $1
    `, [responseId]);

    let dealId = row.deal_id;
    let createdDeal = false;

    if (!dealId) {
      const dealRes = await client.query(`
        INSERT INTO deals (
          response_id,
          cargo_id,
          truck_id,
          status
        )
        VALUES ($1,$2,$3,'active')
        RETURNING id
      `, [responseId, row.cargo_id, row.truck_id]);

      dealId = dealRes.rows[0].id;
      createdDeal = true;
    }

    await client.query(`
      UPDATE cargo
      SET status = 'booked'
      WHERE id = $1
        AND status = 'open'
    `, [row.cargo_id]);

    await client.query(`
      INSERT INTO audit_log (user_id, deal_id, cargo_id, action, payload)
      VALUES ($1, $2, $3, 'response_accepted', $4::jsonb)
    `, [
      user.user_id,
      dealId,
      row.cargo_id,
      JSON.stringify({
        response_id: responseId,
        truck_id: row.truck_id,
        source: "web_cabinet"
      })
    ]);

    if (createdDeal) {
      await client.query(`
        INSERT INTO audit_log (user_id, deal_id, cargo_id, action, payload)
        VALUES ($1, $2, $3, 'deal_created', $4::jsonb)
      `, [
        user.user_id,
        dealId,
        row.cargo_id,
        JSON.stringify({
          response_id: responseId,
          truck_id: row.truck_id,
          source: "web_cabinet"
        })
      ]);
    }

    const historyCount = await client.query(`
      SELECT COUNT(*)::int AS cnt
      FROM deal_status_history
      WHERE deal_id = $1
    `, [dealId]);

    if (!historyCount.rows[0].cnt) {
      await client.query(`
        INSERT INTO deal_status_history (deal_id, status, created_by)
        VALUES
          ($1, 'active', $2),
          ($1, 'driver_assigned', $2)
      `, [dealId, user.user_id]);
    }

    await client.query("COMMIT");

    if (row.driver_telegram_id && BOT_TOKEN) {
      try {
        await fetch(`https://api.telegram.org/bot${BOT_TOKEN}/sendMessage`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            chat_id: row.driver_telegram_id,
            text:
              `✅ Ваш отклик принят!\n\n` +
              `📦 Груз #${row.cargo_id}\n` +
              `🚩 ${row.from_city} → ${row.to_city}\n` +
              `⚖️ ${row.weight_kg || 0} кг\n` +
              `📦 ${row.volume_m3 || 0} м³\n` +
              `🔢 ${row.places_count || 0} мест\n` +
              `💰 ${row.price_amount || "-"} ${row.price_currency || "RUB"}\n` +
              `💵 ${row.rate_per_km || "-"} ₽/км\n\n` +
              `🤝 Сделка #${dealId} создана`,
            reply_markup: {
              inline_keyboard: [
                [
                  { text: "🤝 Открыть сделку", web_app: { url: `https://dalnoboybros.ru/cabinet/?deal_id=${dealId}` } }
                ],
                [
                  { text: "💬 Чат сделки", url: `https://t.me/dalnoboybros_bot?start=dealchat_${dealId}` }
                ]
              ]
            }
          })
        });
      } catch(e) {
        console.error("telegram accept response notify error", e);
      }
    }

    io.emit("response_status_updated", {
      response_id: responseId,
      status: "accepted",
      cargo_id: row.cargo_id,
      truck_id: row.truck_id,
      deal_id: dealId,
      updated_at: new Date().toISOString()
    });

    return res.json({
      success:true,
      response_id:responseId,
      status:"accepted",
      deal_id:dealId
    });

  } catch (err) {
    try { await client.query("ROLLBACK"); } catch(e) {}
    console.error("app response-action error", err);
    return res.status(500).json({success:false,error:"server error"});
  } finally {
    client.release();
  }
});



app.get("/api/app/my-deals", async (req, res) => {
  try {
    const auth = req.headers.authorization || "";
    if (!auth.startsWith("Bearer ")) {
      return res.status(401).json({ success: false, error: "token required" });
    }

    const token = auth.slice(7).trim();
    if (!token) {
      return res.status(401).json({ success: false, error: "token required" });
    }

    const tokenHash = crypto.createHash("sha256").update(token).digest("hex");

    const sessionResult = await pool.query(`
      SELECT s.user_id, u.banned
      FROM app_sessions s
      JOIN users u ON u.id = s.user_id
      WHERE s.token_hash = $1
        AND s.revoked_at IS NULL
        AND s.expires_at > now()
      LIMIT 1
    `, [tokenHash]);

    if (!sessionResult.rows.length) {
      return res.status(401).json({ success: false, error: "invalid session" });
    }

    const user = sessionResult.rows[0];

    if (user.banned) {
      return res.status(403).json({ success: false, error: "user banned" });
    }

    const dealsResult = await pool.query(`
      SELECT
        d.id,
        d.cargo_id,
        d.truck_id,
        CASE
          WHEN c.created_by = $1 THEN 'shipper'
          WHEN t.driver_id = $1 THEN 'carrier'
          ELSE 'viewer'
        END AS side,
        c.from_city,
        c.to_city,
        d.status,
        d.safe_deal_status,
        d.payment_status,
        CASE WHEN d.client_price IS NULL THEN NULL ELSE ROUND(d.client_price::numeric, 0)::text END AS client_price,
        CASE WHEN d.carrier_price IS NULL THEN NULL ELSE ROUND(d.carrier_price::numeric, 0)::text END AS carrier_price,
        CASE WHEN d.dispatcher_profit IS NULL THEN NULL ELSE ROUND(d.dispatcher_profit::numeric, 0)::text END AS dispatcher_profit,
        CASE WHEN c.price_amount IS NULL THEN NULL ELSE ROUND(c.price_amount::numeric, 0)::text END AS cargo_price,
        c.price_currency,
        shipper.full_name AS shipper_name,
        carrier.full_name AS carrier_name,
        t.driver_id AS carrier_user_id,
        d.created_at,
        d.updated_at,
        my_review.id AS my_review_id
      FROM deals d
      LEFT JOIN cargo c ON c.id = d.cargo_id
      LEFT JOIN trucks t ON t.id = d.truck_id
      LEFT JOIN users shipper ON shipper.id = c.created_by
      LEFT JOIN users carrier ON carrier.id = t.driver_id
      LEFT JOIN reviews my_review ON my_review.deal_id = d.id
        AND my_review.from_user_id = $1
        AND my_review.deleted_at IS NULL
      WHERE c.created_by = $1 OR t.driver_id = $1
      ORDER BY d.id DESC
      LIMIT 100
    `, [user.user_id]);

    return res.json({
      success: true,
      deals: dealsResult.rows
    });
  } catch (err) {
    console.error("app my-deals error", err);
    return res.status(500).json({ success: false, error: "server error" });
  }
});





app.post("/api/app/deal-close", async (req, res) => {
  const client = await pool.connect();

  try {
    const auth = req.headers.authorization || "";
    if (!auth.startsWith("Bearer ")) {
      return res.status(401).json({ success: false, error: "token required" });
    }

    const dealId = parseInt((req.body || {}).deal_id, 10);
    if (!dealId) {
      return res.status(400).json({ success: false, error: "deal_id required" });
    }

    const token = auth.slice(7).trim();
    const tokenHash = crypto.createHash("sha256").update(token).digest("hex");

    await client.query("BEGIN");

    const sess = await client.query(`
      SELECT s.user_id
      FROM app_sessions s
      JOIN users u ON u.id = s.user_id
      WHERE s.token_hash = $1
        AND s.revoked_at IS NULL
        AND s.expires_at > now()
        AND COALESCE(u.banned,false) = false
      LIMIT 1
    `, [tokenHash]);

    if (!sess.rows.length) {
      await client.query("ROLLBACK");
      return res.status(401).json({ success: false, error: "session invalid" });
    }

    const userId = sess.rows[0].user_id;

    const deal = await client.query(`
      SELECT d.id, d.cargo_id, d.status, c.created_by AS owner_id
      FROM deals d
      JOIN cargo c ON c.id = d.cargo_id
      WHERE d.id = $1
      FOR UPDATE OF d
    `, [dealId]);

    if (!deal.rows.length) {
      await client.query("ROLLBACK");
      return res.status(404).json({ success: false, error: "deal not found" });
    }

    const row = deal.rows[0];

    if (String(row.owner_id) !== String(userId)) {
      await client.query("ROLLBACK");
      return res.status(403).json({ success: false, error: "access denied" });
    }

    if (row.status === "closed") {
      await client.query("COMMIT");
      return res.json({ success: true, status: "closed", already_closed: true });
    }

    if (!["delivered", "done"].includes(row.status)) {
      await client.query("ROLLBACK");
      return res.status(400).json({ success: false, error: "not ready to close" });
    }

    await client.query(`
      UPDATE deals
      SET status = 'closed', updated_at = now()
      WHERE id = $1
    `, [dealId]);

    await client.query(`
      UPDATE cargo
      SET status = 'done'
      WHERE id = $1
    `, [row.cargo_id]);

    await client.query(`
      INSERT INTO deal_status_history (deal_id, status, created_by)
      VALUES ($1, 'closed', $2)
    `, [dealId, userId]);

    await client.query(`
      INSERT INTO audit_log (user_id, deal_id, cargo_id, action, payload)
      VALUES ($1, $2, $3, 'deal_status_changed', $4::jsonb)
    `, [
      userId,
      dealId,
      row.cargo_id,
      JSON.stringify({ status: "closed", source: "web_cabinet" })
    ]);

    await client.query("COMMIT");

    return res.json({ success: true, deal_id: dealId, status: "closed" });
  } catch (err) {
    try { await client.query("ROLLBACK"); } catch (_) {}
    console.error("app deal-close error", err);
    return res.status(500).json({ success: false, error: "server error" });
  } finally {
    client.release();
  }
});


app.post("/api/app/deal-review", async (req, res) => {
  const client = await pool.connect();
  try {
    const auth = req.headers.authorization || "";
    if (!auth.startsWith("Bearer ")) {
      return res.status(401).json({success:false,error:"token required"});
    }

    const b = req.body || {};
    const dealId = parseInt(b.deal_id, 10);
    const score = parseInt(b.score, 10);
    const comment = String(b.comment || "").trim();

    if (!dealId) return res.status(400).json({success:false,error:"deal_id required"});
    if (!score || score < 1 || score > 5) return res.status(400).json({success:false,error:"score must be 1..5"});
    if (comment.length < 3) return res.status(400).json({success:false,error:"comment too short"});

    const tokenHash = crypto.createHash("sha256").update(auth.slice(7).trim()).digest("hex");

    await client.query("BEGIN");

    const sess = await client.query(`
      SELECT s.user_id
      FROM app_sessions s
      JOIN users u ON u.id = s.user_id
      WHERE s.token_hash = $1
        AND s.revoked_at IS NULL
        AND s.expires_at > now()
        AND COALESCE(u.banned,false) = false
      LIMIT 1
    `, [tokenHash]);

    if (!sess.rows.length) {
      await client.query("ROLLBACK");
      return res.status(401).json({success:false,error:"session invalid"});
    }

    const userId = sess.rows[0].user_id;

    const dealRes = await client.query(`
      SELECT d.id,d.cargo_id,d.status,c.created_by AS shipper_id,
             COALESCE(r.driver_id,t.driver_id) AS carrier_id
      FROM deals d
      JOIN cargo c ON c.id=d.cargo_id
      JOIN trucks t ON t.id=d.truck_id
      LEFT JOIN responses r ON r.id=d.response_id
      WHERE d.id=$1
    `, [dealId]);

    if (!dealRes.rows.length) {
      await client.query("ROLLBACK");
      return res.status(404).json({success:false,error:"deal not found"});
    }

    const deal = dealRes.rows[0];

    if (String(deal.shipper_id) !== String(userId)) {
      await client.query("ROLLBACK");
      return res.status(403).json({success:false,error:"access denied"});
    }

    if (!["delivered","done","closed"].includes(deal.status)) {
      await client.query("ROLLBACK");
      return res.status(400).json({success:false,error:"deal not finished"});
    }

    const old = await client.query(`
      SELECT id FROM reviews
      WHERE deal_id=$1 AND from_user_id=$2 AND deleted_at IS NULL
      LIMIT 1
    `, [dealId,userId]);

    if (old.rows.length) {
      await client.query("ROLLBACK");
      return res.status(400).json({success:false,error:"review already exists"});
    }

    const complaint = score <= 2;

    await client.query(`
      INSERT INTO reviews
      (deal_id,from_company_id,to_company_id,from_user_id,to_user_id,review_type,overall_score,comment,is_complaint)
      VALUES ($1,1,1,$2,$3,'carrier',$4,$5,$6)
    `, [dealId,userId,deal.carrier_id,score,comment,complaint]);

    await client.query(`
      INSERT INTO audit_log (user_id,deal_id,cargo_id,action,payload)
      VALUES ($1,$2,$3,'review_created',$4::jsonb)
    `, [userId,dealId,deal.cargo_id,JSON.stringify({
      to_user_id:deal.carrier_id,
      score:score,
      review_type:"carrier",
      is_complaint:complaint,
      comment_len:comment.length,
      source:"web_cabinet"
    })]);

    await client.query("COMMIT");
    return res.json({success:true,deal_id:dealId,score:score});
  } catch (err) {
    try { await client.query("ROLLBACK"); } catch (_) {}
    console.error("app deal-review error", err);
    return res.status(500).json({success:false,error:"server error"});
  } finally {
    client.release();
  }
});




app.get("/api/app/carrier-profile", async (req, res) => {
  try {
    const auth = req.headers.authorization || "";
    if (!auth.startsWith("Bearer ")) {
      return res.status(401).json({success:false,error:"token required"});
    }

    const userId = parseInt(req.query.user_id, 10);
    if (!userId) {
      return res.status(400).json({success:false,error:"user_id required"});
    }

    const tokenHash = crypto.createHash("sha256").update(auth.slice(7).trim()).digest("hex");

    const sess = await pool.query(`
      SELECT s.user_id
      FROM app_sessions s
      JOIN users u ON u.id = s.user_id
      WHERE s.token_hash = $1
        AND s.revoked_at IS NULL
        AND s.expires_at > now()
        AND COALESCE(u.banned,false) = false
      LIMIT 1
    `, [tokenHash]);

    if (!sess.rows.length) {
      return res.status(401).json({success:false,error:"session invalid"});
    }

    const profileRes = await pool.query(`
      SELECT id, full_name, role, verified, plan_type, created_at
      FROM users
      WHERE id = $1
      LIMIT 1
    `, [userId]);

    if (!profileRes.rows.length) {
      return res.status(404).json({success:false,error:"user not found"});
    }

    const statsRes = await pool.query(`
      SELECT
        COUNT(*) AS reviews_count,
        ROUND(AVG(overall_score)::numeric, 2) AS avg_score,
        COUNT(*) FILTER (WHERE is_complaint=true OR overall_score <= 2) AS complaints_count
      FROM reviews
      WHERE to_user_id = $1
        AND deleted_at IS NULL
    `, [userId]);

    const doneRes = await pool.query(`
      SELECT COUNT(*) AS deals_done
      FROM deals d
      JOIN cargo c ON c.id = d.cargo_id
      JOIN trucks t ON t.id = d.truck_id
      LEFT JOIN responses r ON r.id = d.response_id
      WHERE (c.created_by = $1 OR t.driver_id = $1 OR r.driver_id = $1)
        AND d.status IN ('done','delivered','closed')
    `, [userId]);

    const reviewsRes = await pool.query(`
      SELECT r.overall_score, r.comment, r.is_complaint, r.created_at, u.full_name AS from_name
      FROM reviews r
      LEFT JOIN users u ON u.id = r.from_user_id
      WHERE r.to_user_id = $1
        AND r.deleted_at IS NULL
      ORDER BY r.id DESC
      LIMIT 5
    `, [userId]);

    return res.json({
      success:true,
      profile: profileRes.rows[0],
      stats: statsRes.rows[0],
      deals_done: doneRes.rows[0].deals_done,
      reviews: reviewsRes.rows
    });
  } catch (err) {
    console.error("app carrier-profile error", err);
    return res.status(500).json({success:false,error:"server error"});
  }
});



app.get("/api/app/deal-timeline", async (req, res) => {
  try {
    const auth = req.headers.authorization || "";
    if (!auth.startsWith("Bearer ")) {
      return res.status(401).json({ success: false, error: "token required" });
    }

    const dealId = parseInt(req.query.deal_id, 10);
    if (!dealId) {
      return res.status(400).json({ success: false, error: "deal_id required" });
    }

    const token = auth.slice(7).trim();
    const tokenHash = crypto.createHash("sha256").update(token).digest("hex");

    const sessionResult = await pool.query(`
      SELECT s.user_id, u.banned
      FROM app_sessions s
      JOIN users u ON u.id = s.user_id
      WHERE s.token_hash = $1
        AND s.revoked_at IS NULL
        AND s.expires_at > now()
      LIMIT 1
    `, [tokenHash]);

    if (!sessionResult.rows.length) {
      return res.status(401).json({ success: false, error: "invalid session" });
    }

    const user = sessionResult.rows[0];
    if (user.banned) {
      return res.status(403).json({ success: false, error: "user banned" });
    }

    const accessResult = await pool.query(`
      SELECT d.id
      FROM deals d
      LEFT JOIN cargo c ON c.id = d.cargo_id
      LEFT JOIN trucks t ON t.id = d.truck_id
      WHERE d.id = $1
        AND (c.created_by = $2 OR t.driver_id = $2)
      LIMIT 1
    `, [dealId, user.user_id]);

    if (!accessResult.rows.length) {
      return res.status(404).json({ success: false, error: "deal not found" });
    }

    const timelineResult = await pool.query(`
      SELECT
        h.status,
        h.created_at,
        u.full_name AS created_by_name
      FROM deal_status_history h
      LEFT JOIN users u ON u.id = h.created_by
      WHERE h.deal_id = $1
      ORDER BY h.id ASC
    `, [dealId]);

    return res.json({
      success: true,
      deal_id: dealId,
      timeline: timelineResult.rows
    });
  } catch (err) {
    console.error("app deal-timeline error", err);
    return res.status(500).json({ success: false, error: "server error" });
  }
});



app.post("/api/location", async (req, res) => {
  try {
    const { telegram_id, lat, lon } = req.body;

    if (!telegram_id || !lat || !lon) {
      return res.status(400).json({
        success: false,
        error: "telegram_id, lat, lon required"
      });
    }

    const userResult = await pool.query(
      `
      SELECT id
      FROM users
      WHERE telegram_id=$1
      LIMIT 1
      `,
      [telegram_id]
    );

    if (!userResult.rows.length) {
      return res.status(404).json({
        success: false,
        error: "user not found"
      });
    }

    const userId = userResult.rows[0].id;

    const truckResult = await pool.query(
      `
      SELECT id
      FROM trucks
      WHERE driver_id=$1
      ORDER BY id DESC
      LIMIT 1
      `,
      [userId]
    );

    if (!truckResult.rows.length) {
      return res.status(404).json({
        success: false,
        error: "truck not found"
      });
    }

    const truckId = truckResult.rows[0].id;

    await pool.query(
      `
      UPDATE trucks
      SET
        latitude=$1,
        longitude=$2,
        location_updated_at=NOW(),
        status='active'
      WHERE id=$3
      `,
      [lat, lon, truckId]
    );

    res.json({
      success: true,
      truck_id: truckId,
      lat,
      lon
    });

  } catch (e) {
    console.error(e);

    res.status(500).json({
      success: false,
      error: e.message
    });
  }
});




function distanceKm(lat1, lon1, lat2, lon2) {
  const r = 6371;
  const toRad = (v) => Number(v) * Math.PI / 180;

  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);

  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) *
    Math.cos(toRad(lat2)) *
    Math.sin(dLon / 2) ** 2;

  return Math.round(r * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a)));
}





app.get("/api/trucks/active", async (req, res) => {
  try {
    const rows = await pool.query(`
      SELECT
        t.id,
        t.driver_id,
        
        
        t.latitude,
        t.longitude,
        t.location_updated_at,
        CASE
          WHEN t.location_updated_at > now() - interval '30 minutes' THEN 'online'
          WHEN t.location_updated_at > now() - interval '2 hours' THEN 'recent'
          ELSE 'offline'
        END AS online_status,
        u.full_name,
        u.telegram_username
      FROM trucks t
      LEFT JOIN users u ON u.id = t.driver_id
      WHERE t.latitude IS NOT NULL
        AND t.longitude IS NOT NULL
        AND t.status='active'
      ORDER BY t.location_updated_at DESC NULLS LAST
      LIMIT 500
    `);

    res.json({
      success: true,
      count: rows.rows.length,
      items: rows.rows
    });
  } catch (e) {
    console.error(e);
    res.status(500).json({ success: false, error: "server_error" });
  }
});

app.get("/api/cargo/open", async (req, res) => {
  try {
    const minRate = Number(req.query.min_rate || 0);
    const city = (req.query.city || "").trim();
    const profitability = req.query.profitability || "";

    const params = [];
    let where = `
      WHERE status='open'
        AND load_latitude IS NOT NULL
        AND load_longitude IS NOT NULL
        AND NOT EXISTS (
          SELECT 1
          FROM deals d
          WHERE d.cargo_id = cargo.id
            AND d.status IN (
              'active',
              'driver_assigned',
              'to_pickup',
              'loading',
              'loaded',
              'in_progress',
              'breakdown',
              'resume_movement'
            )
        )
    `;

    if (minRate > 0) {
      params.push(minRate);
      where += ` AND COALESCE(rate_per_km, 0) >= $${params.length}`;
    }

    if (city) {
      params.push(`%${city}%`);
      where += ` AND (from_city ILIKE $${params.length} OR to_city ILIKE $${params.length})`;
    }

    if (profitability === "profitable") {
      where += ` AND COALESCE(rate_per_km, 0) > 0`;
    }

    const rows = await pool.query(`
      SELECT
        id,
        from_city,
        to_city,
        description,
        price_amount,
        price_currency,
        distance_km,
        rate_per_km,
        weight_kg,
        volume_m3,
        places_count,
        cargo_type,
        load_latitude,
        load_longitude,
        unload_latitude,
        unload_longitude
      FROM cargo
      ${where}
      ORDER BY
        COALESCE(rate_per_km, 0) DESC,
        id DESC
      LIMIT 500
    `, params);

    res.json({
      success: true,
      count: rows.rows.length,
      filters: {
        min_rate: minRate,
        city,
        profitability
      },
      items: rows.rows
    });
  } catch (e) {
    console.error(e);
    res.status(500).json({ success: false, error: "server_error" });
  }
});

app.get("/api/nearby", async (req, res) => {
  try {
    const telegramId = req.query.telegram_id;
    const requestedRadius = Number(req.query.radius || 50);
    const profitabilityFilter = req.query.profitability || null;

    if (!telegramId) {
      return res.status(400).json({
        success: false,
        error: "telegram_id required"
      });
    }

    const truckResult = await pool.query(
      `
      SELECT
        t.latitude,
        t.longitude,
        t.min_rate_per_km,
        t.search_radius_km
      FROM trucks t
      JOIN users u ON u.id = t.driver_id
      WHERE u.telegram_id=$1
      ORDER BY t.id DESC
      LIMIT 1
      `,
      [telegramId]
    );

    if (!truckResult.rows.length || !truckResult.rows[0].latitude || !truckResult.rows[0].longitude) {
      return res.status(404).json({
        success: false,
        error: "truck location not found"
      });
    }

    const truck = truckResult.rows[0];
    const dbRadius = Number(truck.search_radius_km || 50);
    const rawRadius = req.query.radius !== undefined ? requestedRadius : dbRadius;
    const radius = Math.max(1, Math.min(Number(rawRadius || 50), 1000));

    const cargoResult = await pool.query(
      `
      SELECT
        id,
        from_city,
        to_city,
        description,
        price_amount,
        price_currency,
        distance_km,
        rate_per_km,
        load_latitude,
        load_longitude,
        unload_latitude,
        unload_longitude
      FROM cargo
      WHERE status='open'
        AND load_latitude IS NOT NULL
        AND load_longitude IS NOT NULL
        AND NOT EXISTS (
          SELECT 1
          FROM deals d
          WHERE d.cargo_id = cargo.id
            AND d.status IN (
              'active',
              'driver_assigned',
              'to_pickup',
              'loading',
              'loaded',
              'in_progress',
              'breakdown',
              'resume_movement'
            )
        )
      ORDER BY id DESC
      LIMIT 100
      `
    );

    const items = cargoResult.rows
      .map((c) => {
        const distance_km = distanceKm(
          truck.latitude,
          truck.longitude,
          c.load_latitude,
          c.load_longitude
        );

        const cargoRate = Number(c.rate_per_km || 0);
        const minRate = Number(truck.min_rate_per_km || 0);

        let profitability = "unknown";
        let profit_delta = null;

        if (cargoRate > 0 && minRate > 0) {
          profit_delta = Math.round((cargoRate - minRate) * 100) / 100;
          profitability = cargoRate >= minRate ? "profitable" : "low";
        }

        return {
          ...c,
          distance_km,
          truck_min_rate_per_km: truck.min_rate_per_km,
          profitability,
          profit_delta
        };
      })
      .filter((c) => c.distance_km <= radius)
      .filter((c) => !profitabilityFilter || c.profitability === profitabilityFilter)
      .sort((a, b) => {
        const order = { profitable: 0, unknown: 1, low: 2 };
        const pa = order[a.profitability] ?? 1;
        const pb = order[b.profitability] ?? 1;

        if (pa !== pb) return pa - pb;
        return a.distance_km - b.distance_km;
      });

    res.json({
      success: true,
      radius,
      count: items.length,
      items
    });

  } catch (e) {
    console.error(e);

    res.status(500).json({
      success: false,
      error: e.message
    });
  }
});






// === ROUTE_ADDONS_HELPERS_V1 ===
function projectRouteKm(baseLat, baseLon, lat, lon) {
  const x = (Number(lon) - Number(baseLon)) * 111.320 * Math.cos(Number(baseLat) * Math.PI / 180);
  const y = (Number(lat) - Number(baseLat)) * 110.574;
  return { x, y };
}

function distancePointToRouteKm(pointLat, pointLon, startLat, startLon, endLat, endLon) {
  const p = projectRouteKm(startLat, startLon, pointLat, pointLon);
  const b = projectRouteKm(startLat, startLon, endLat, endLon);
  const ab2 = b.x * b.x + b.y * b.y;
  if (!ab2) return Math.sqrt(p.x * p.x + p.y * p.y);

  let t = (p.x * b.x + p.y * b.y) / ab2;
  t = Math.max(0, Math.min(1, t));

  const dx = p.x - b.x * t;
  const dy = p.y - b.y * t;
  return Math.round(Math.sqrt(dx * dx + dy * dy) * 10) / 10;
}
// === END ROUTE_ADDONS_HELPERS_V1 ===


// === ROUTE_ADDONS_API_V1 ===
app.get("/api/route-addons", async (req, res) => {
  try {
    const telegramId = req.query.telegram_id;
    const corridorKm = Math.max(1, Math.min(Number(req.query.corridor || 50), 300));

    if (!telegramId) {
      return res.status(400).json({ success: false, error: "telegram_id_required" });
    }

    const activeDeal = await pool.query(`
      SELECT
        d.id AS deal_id,
        d.cargo_id AS main_cargo_id,
        d.truck_id,
        d.status AS deal_status,
        c.from_city AS route_from_city,
        c.to_city AS route_to_city,
        c.load_latitude,
        c.load_longitude,
        c.unload_latitude,
        c.unload_longitude
      FROM deals d
      JOIN cargo c ON c.id = d.cargo_id
      JOIN trucks t ON t.id = d.truck_id
      JOIN users u ON u.id = t.driver_id
      WHERE u.telegram_id = $1
        AND d.status IN (
          'active',
          'driver_assigned',
          'to_pickup',
          'loading',
          'loaded',
          'in_progress',
          'breakdown',
          'resume_movement'
        )
        AND c.load_latitude IS NOT NULL
        AND c.load_longitude IS NOT NULL
        AND c.unload_latitude IS NOT NULL
        AND c.unload_longitude IS NOT NULL
      ORDER BY d.id DESC
      LIMIT 1
    `, [telegramId]);

    if (!activeDeal.rows.length) {
      return res.json({
        success: true,
        no_active_route: true,
        message: "active_route_not_found",
        count: 0,
        items: []
      });
    }

    const route = activeDeal.rows[0];

    const cargoResult = await pool.query(`
      SELECT
        id,
        from_city,
        to_city,
        description,
        price_amount,
        price_currency,
        distance_km,
        rate_per_km,
        weight_kg,
        volume_m3,
        places_count,
        cargo_type,
        load_latitude,
        load_longitude,
        unload_latitude,
        unload_longitude
      FROM cargo
      WHERE status = 'open'
        AND id <> $1
        AND COALESCE(cargo_type, 'full') = 'partial'
        AND load_latitude IS NOT NULL
        AND load_longitude IS NOT NULL
        AND NOT EXISTS (
          SELECT 1
          FROM deals d
          WHERE d.cargo_id = cargo.id
            AND d.status IN (
              'active',
              'driver_assigned',
              'to_pickup',
              'loading',
              'loaded',
              'in_progress',
              'breakdown',
              'resume_movement'
            )
        )
      ORDER BY id DESC
      LIMIT 300
    `, [route.main_cargo_id]);

    const items = cargoResult.rows
      .map((c) => ({
        ...c,
        route_distance_km: distancePointToRouteKm(
          c.load_latitude,
          c.load_longitude,
          route.load_latitude,
          route.load_longitude,
          route.unload_latitude,
          route.unload_longitude
        ),
        route_deal_id: route.deal_id,
        main_cargo_id: route.main_cargo_id,
        route_from_city: route.route_from_city,
        route_to_city: route.route_to_city
      }))
      .filter((c) => Number(c.route_distance_km) <= corridorKm)
      .sort((a, b) => Number(a.route_distance_km || 0) - Number(b.route_distance_km || 0));

    return res.json({
      success: true,
      no_active_route: false,
      corridor_km: corridorKm,
      route,
      count: items.length,
      items
    });
  } catch (e) {
    console.error("api route-addons error", e);
    return res.status(500).json({ success: false, error: "server_error" });
  }
});
// === END ROUTE_ADDONS_API_V1 ===

app.post("/api/cargo/create", async (req, res) => {
  try {
    const {
      telegram_id,
      from_city,
      to_city,
      description,
      price_amount,
      load_latitude,
      load_longitude,
      unload_latitude,
      unload_longitude,
      distance_km,
      weight_tons,
      weight_kg,
      volume_m3,
      places_count,
      cargo_type
    } = req.body;

    let createdBy = null;

    if (telegram_id) {
      const userRes = await pool.query(
        "SELECT id FROM users WHERE telegram_id=$1 LIMIT 1",
        [telegram_id]
      );

      if (userRes.rows.length) {
        createdBy = userRes.rows[0].id;
      }
    }

    const price = Number(price_amount || 0);
    const dist = Number(distance_km || 0);
    const rate = dist > 0 ? Math.round((price / dist) * 100) / 100 : null;

    const weightKg = Number(weight_kg || 0);
    const weightTons = weight_tons !== undefined && weight_tons !== null
      ? Number(weight_tons || 0)
      : (weightKg > 0 ? Math.round((weightKg / 1000) * 1000) / 1000 : 0);

    const volume = Number(volume_m3 || 0);
    const places = Number(places_count || 0);
    const type = cargo_type || 'full';

    const q = await pool.query(`
      INSERT INTO cargo (
        from_city,
        to_city,
        description,
        price_amount,
        load_latitude,
        load_longitude,
        unload_latitude,
        unload_longitude,
        distance_km,
        rate_per_km,
        weight_tons,
        weight_kg,
        volume_m3,
        places_count,
        cargo_type,
        created_by,
        status
      )
      VALUES (
        $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,'open'
      )
      RETURNING
        id,
        from_city,
        to_city,
        description,
        price_amount,
        price_currency,
        distance_km,
        rate_per_km,
        load_latitude,
        load_longitude,
        unload_latitude,
        unload_longitude,
        weight_tons,
        weight_kg,
        volume_m3,
        places_count,
        cargo_type,
        created_by,
        status
    `, [
      from_city,
      to_city,
      description,
      price,
      load_latitude,
      load_longitude,
      unload_latitude,
      unload_longitude,
      dist,
      rate,
      weightTons,
      weightKg,
      volume,
      places,
      type,
      createdBy
    ]);

    io.emit("cargo_created", q.rows[0]);

    res.json({
      success: true,
      cargo_id: q.rows[0].id,
      rate_per_km: q.rows[0].rate_per_km,
      cargo: q.rows[0]
    });

  } catch(e) {
    console.error(e);
    res.status(500).json({ success:false, error:"server_error" });
  }
});



app.get("/api/trucks/available", async (req, res) => {
  try {
    const tons = Number(req.query.tons || 0);
    const volume = Number(req.query.volume || 0);
    const from = (req.query.from || "").trim();
    const to = (req.query.to || "").trim();

    const params = [];
    let where = `
      WHERE t.status='active'
        AND t.allow_partial_load = true
        AND t.latitude IS NOT NULL
        AND t.longitude IS NOT NULL
        AND t.location_updated_at > now() - interval '24 hours'
    `;

    if (tons > 0) {
      params.push(tons);
      where += ` AND COALESCE(t.available_tons, 0) >= $${params.length}`;
    }

    if (volume > 0) {
      params.push(volume);
      where += ` AND COALESCE(t.available_volume_m3, 0) >= $${params.length}`;
    }

    if (from) {
      params.push(`%${from}%`);
      where += ` AND (t.route_from ILIKE $${params.length} OR t.current_city ILIKE $${params.length})`;
    }

    if (to) {
      params.push(`%${to}%`);
      where += ` AND t.route_to ILIKE $${params.length}`;
    }

    const rows = await pool.query(`
      SELECT
        t.id,
        t.driver_id,
        t.current_city,
        t.body_type,
        t.capacity_tons,
        t.volume_m3,
        t.available_tons,
        t.available_volume_m3,
        t.route_from,
        t.route_to,
        t.latitude,
        t.longitude,
        t.location_updated_at,
        CASE
          WHEN t.location_updated_at > now() - interval '30 minutes' THEN 'online'
          WHEN t.location_updated_at > now() - interval '2 hours' THEN 'recent'
          ELSE 'offline'
        END AS online_status,
        u.full_name,
        COALESCE(u.plan_type, 'free') AS plan_type,
        (
          40
          + CASE WHEN COALESCE(t.available_tons, 0) >= COALESCE($1::numeric, 0) THEN 20 ELSE 0 END
          + CASE WHEN COALESCE(t.available_volume_m3, 0) >= COALESCE($2::numeric, 0) THEN 20 ELSE 0 END
          + CASE WHEN t.location_updated_at > now() - interval '2 hours' THEN 10 ELSE 0 END
          + CASE WHEN COALESCE(u.plan_type, 'free') IN ('pro','company') THEN 10 ELSE 0 END
        ) AS match_score
      FROM trucks t
      LEFT JOIN users u ON u.id = t.driver_id
      ${where}
      ORDER BY t.location_updated_at DESC NULLS LAST
      LIMIT 200
    `, params);

    res.json({
      success: true,
      count: rows.rows.length,
      filters: { tons, volume, from, to },
      items: rows.rows
    });
  } catch (e) {
    console.error(e);
    res.status(500).json({ success: false, error: e.message });
  }
});


app.get("/api/trucks/available", async (req, res) => {
  try {
    const tons = Number(req.query.tons || 0);
    const volume = Number(req.query.volume || 0);
    const from = (req.query.from || "").trim();
    const to = (req.query.to || "").trim();

    const params = [];
    let where = `
      WHERE t.status='active'
        AND t.allow_partial_load = true
        AND t.latitude IS NOT NULL
        AND t.longitude IS NOT NULL
        AND t.location_updated_at > now() - interval '24 hours'
    `;

    if (tons > 0) {
      params.push(tons);
      where += ` AND COALESCE(t.available_tons, 0) >= $${params.length}`;
    }

    if (volume > 0) {
      params.push(volume);
      where += ` AND COALESCE(t.available_volume_m3, 0) >= $${params.length}`;
    }

    if (from) {
      params.push(`%${from}%`);
      where += ` AND (t.route_from ILIKE $${params.length} OR t.current_city ILIKE $${params.length})`;
    }

    if (to) {
      params.push(`%${to}%`);
      where += ` AND t.route_to ILIKE $${params.length}`;
    }

    const rows = await pool.query(`
      SELECT
        t.id,
        t.driver_id,
        t.current_city,
        t.body_type,
        t.capacity_tons,
        t.volume_m3,
        t.available_tons,
        t.available_volume_m3,
        t.route_from,
        t.route_to,
        t.latitude,
        t.longitude,
        t.location_updated_at,
        CASE
          WHEN t.location_updated_at > now() - interval '30 minutes' THEN 'online'
          WHEN t.location_updated_at > now() - interval '2 hours' THEN 'recent'
          ELSE 'offline'
        END AS online_status,
        u.full_name,
        COALESCE(u.plan_type, 'free') AS plan_type,
        (
          40
          + CASE WHEN COALESCE(t.available_tons, 0) >= COALESCE($1::numeric, 0) THEN 20 ELSE 0 END
          + CASE WHEN COALESCE(t.available_volume_m3, 0) >= COALESCE($2::numeric, 0) THEN 20 ELSE 0 END
          + CASE WHEN t.location_updated_at > now() - interval '2 hours' THEN 10 ELSE 0 END
          + CASE WHEN COALESCE(u.plan_type, 'free') IN ('pro','company') THEN 10 ELSE 0 END
        ) AS match_score
      FROM trucks t
      LEFT JOIN users u ON u.id = t.driver_id
      ${where}
      ORDER BY t.location_updated_at DESC NULLS LAST
      LIMIT 200
    `, params);

    res.json({
      success: true,
      count: rows.rows.length,
      filters: { tons, volume, from, to },
      items: rows.rows
    });
  } catch (e) {
    console.error(e);
    res.status(500).json({ success: false, error: e.message });
  }
});


app.get("/api/cargo/:id/available-trucks", async (req, res) => {
  try {
    const cargoId = Number(req.params.id);

    const cargo = await pool.query(`
      SELECT
        id,
        from_city,
        to_city,
        weight_tons,
        volume_m3,
        rate_per_km,
        load_latitude,
        load_longitude
      FROM cargo
      WHERE id=$1
      LIMIT 1
    `, [cargoId]);

    if (!cargo.rows.length) {
      return res.status(404).json({ success:false, error:"cargo_not_found" });
    }

    const c = cargo.rows[0];

    const rows = await pool.query(`
      SELECT
        t.id,
        t.driver_id,
        t.current_city,
        t.body_type,
        t.capacity_tons,
        t.volume_m3,
        t.available_tons,
        t.available_volume_m3,
        t.route_from,
        t.route_to,
        t.latitude,
        t.longitude,
        t.location_updated_at,
        t.min_rate_per_km,
        u.full_name,
        COALESCE(u.plan_type, 'free') AS plan_type,
        ROUND(
          (
            6371 * acos(
              cos(radians($3)) * cos(radians(t.latitude)) *
              cos(radians(t.longitude) - radians($4)) +
              sin(radians($3)) * sin(radians(t.latitude))
            )
          )::numeric, 1
        ) AS distance_km,
        CEIL((
          (
            6371 * acos(
              cos(radians($3)) * cos(radians(t.latitude)) *
              cos(radians(t.longitude) - radians($4)) +
              sin(radians($3)) * sin(radians(t.latitude))
            )
          ) / 70.0 * 60
        )::numeric) AS eta_minutes,
        LEAST(100,
          (
            CASE
              WHEN (
                6371 * acos(
                  cos(radians($3)) * cos(radians(t.latitude)) *
                  cos(radians(t.longitude) - radians($4)) +
                  sin(radians($3)) * sin(radians(t.latitude))
                )
              ) <= 50 THEN 25
              WHEN (
                6371 * acos(
                  cos(radians($3)) * cos(radians(t.latitude)) *
                  cos(radians(t.longitude) - radians($4)) +
                  sin(radians($3)) * sin(radians(t.latitude))
                )
              ) <= 150 THEN 18
              WHEN (
                6371 * acos(
                  cos(radians($3)) * cos(radians(t.latitude)) *
                  cos(radians(t.longitude) - radians($4)) +
                  sin(radians($3)) * sin(radians(t.latitude))
                )
              ) <= 300 THEN 10
              ELSE 5
            END
            + CASE
                WHEN t.location_updated_at > now() - interval '30 minutes' THEN 15
                WHEN t.location_updated_at > now() - interval '2 hours' THEN 8
                ELSE 0
              END
            + CASE WHEN COALESCE(t.capacity_tons, 0) >= COALESCE($1::numeric, 0) THEN 15 ELSE 0 END
            + CASE WHEN COALESCE(t.volume_m3, 0) >= COALESCE($2::numeric, 0) THEN 15 ELSE 0 END
            + CASE WHEN COALESCE(u.plan_type, 'free') IN ('pro','company') THEN 10 ELSE 0 END
            + CASE
                WHEN COALESCE($5::numeric, 0) > 0
                 AND COALESCE(t.min_rate_per_km, 0) > 0
                 AND COALESCE($5::numeric, 0) >= COALESCE(t.min_rate_per_km, 0)
                THEN 20
                WHEN COALESCE($5::numeric, 0) > 0
                 AND COALESCE(t.min_rate_per_km, 0) = 0
                THEN 8
                ELSE 0
              END
          )
        ) AS match_score
      FROM trucks t
      LEFT JOIN users u ON u.id = t.driver_id
      WHERE t.status='active'
        AND t.latitude IS NOT NULL
        AND t.longitude IS NOT NULL
        AND t.location_updated_at > now() - interval '24 hours'
      ORDER BY eta_minutes ASC, match_score DESC
      LIMIT 50
    `, [
      c.weight_tons,
      c.volume_m3,
      c.load_latitude,
      c.load_longitude,
      c.rate_per_km
    ]);

    res.json({
      success:true,
      cargo:c,
      count: rows.rows.length,
      items: rows.rows
    });
  } catch(e) {
    console.error(e);
    res.status(500).json({ success:false, error:e.message });
  }
});


app.get("/api/matching/open-cargo", async (req, res) => {
  try {
    const rows = await pool.query(`
      SELECT
        c.id AS cargo_id,
        c.from_city,
        c.to_city,
        c.weight_tons,
        c.volume_m3,
        c.price_amount,
        c.rate_per_km,
        t.id AS truck_id,
        t.driver_id,
        t.current_city,
        t.body_type,
        t.available_tons,
        t.available_volume_m3,
        t.route_from,
        t.route_to,
        t.latitude,
        t.longitude,
        u.full_name,
        (
          40
          + CASE WHEN COALESCE(t.available_tons, 0) >= COALESCE(c.weight_tons, 0) THEN 20 ELSE 0 END
          + CASE WHEN COALESCE(t.available_volume_m3, 0) >= COALESCE(c.volume_m3, 0) THEN 20 ELSE 0 END
          + CASE WHEN t.location_updated_at > now() - interval '2 hours' THEN 10 ELSE 0 END
          + CASE WHEN COALESCE(u.plan_type, 'free') IN ('pro','company') THEN 10 ELSE 0 END
        ) AS match_score
      FROM cargo c
      JOIN trucks t ON t.allow_partial_load=true
      LEFT JOIN users u ON u.id = t.driver_id
      WHERE c.status='open'
        AND t.status='active'
        AND t.latitude IS NOT NULL
        AND t.longitude IS NOT NULL
        AND t.location_updated_at > now() - interval '24 hours'
        AND COALESCE(t.available_tons, 0) >= COALESCE(c.weight_tons, 0)
        AND COALESCE(t.available_volume_m3, 0) >= COALESCE(c.volume_m3, 0)
        AND (t.route_from ILIKE '%' || c.from_city || '%' OR t.current_city ILIKE '%' || c.from_city || '%')
        AND t.route_to ILIKE '%' || c.to_city || '%'
      ORDER BY match_score DESC, c.id DESC
      LIMIT 200
    `);

    res.json({
      success: true,
      count: rows.rows.length,
      items: rows.rows
    });
  } catch (e) {
    console.error(e);
    res.status(500).json({ success:false, error:e.message });
  }
});





app.get("/api/me/truck", async (req, res) => {
  try {
    const telegramId = req.query.telegram_id;

    if (!telegramId) {
      return res.status(400).json({
        success: false,
        error: "telegram_id_required"
      });
    }

    const result = await pool.query(`
      SELECT
        t.id,
        t.driver_id,
        t.current_city,
        t.body_type,
        t.status,
        t.capacity_tons,
        t.volume_m3,
        t.length_m,
        t.comment,
        t.latitude,
        t.longitude,
        t.location_updated_at,
        t.min_rate_per_km,
        t.search_radius_km,
        t.notifications_enabled,
        t.notify_profitable_only,
        t.available_tons,
        t.available_volume_m3,
        t.allow_partial_load,
        t.route_from,
        t.route_to,
        t.photo_url,
        u.telegram_id,
        u.full_name,
        u.telegram_username
      FROM users u
      LEFT JOIN LATERAL (
        SELECT *
        FROM trucks t
        WHERE t.driver_id = u.id
        ORDER BY
          CASE WHEN t.status = 'active' THEN 0 ELSE 1 END,
          t.id DESC
        LIMIT 1
      ) t ON true
      WHERE u.telegram_id = $1
      LIMIT 1
    `, [telegramId]);

    if (!result.rows.length) {
      return res.status(404).json({
        success: false,
        error: "user_not_found"
      });
    }

    const row = result.rows[0];

    if (!row.id) {
      return res.status(404).json({
        success: false,
        error: "truck_not_found"
      });
    }

    return res.json({
      success: true,
      truck: row
    });
  } catch (e) {
    console.error("api me truck error", e);
    return res.status(500).json({
      success: false,
      error: "server_error"
    });
  }
});

app.post("/api/truck/location", async (req, res) => {
  try {
    const telegramId = req.body.telegram_id;
    const lat = Number(req.body.lat);
    const lon = Number(req.body.lon);

    if (!telegramId || !lat || !lon) {
      return res.status(400).json({ success:false, error:"telegram_id_lat_lon_required" });
    }

    const userResult = await pool.query(`
      SELECT id FROM users WHERE telegram_id=$1 LIMIT 1
    `, [telegramId]);

    if (!userResult.rows.length) {
      return res.status(404).json({ success:false, error:"user_not_found" });
    }

    const userId = userResult.rows[0].id;

    const truckResult = await pool.query(`
      UPDATE trucks
      SET latitude=$1,
          longitude=$2,
          location_updated_at=now(),
          status='active'
      WHERE id=(
        SELECT id FROM trucks
        WHERE driver_id=$3
        ORDER BY id DESC
        LIMIT 1
      )
      RETURNING id
    `, [lat, lon, userId]);

    if (!truckResult.rows.length) {
      return res.status(404).json({ success:false, error:"truck_not_found" });
    }

    io.emit("truck_location_updated", {
      truck_id: truckResult.rows[0].id,
      telegram_id: telegramId,
      latitude: lat,
      longitude: lon,
      updated_at: new Date().toISOString()
    });

    res.json({ success:true, truck_id: truckResult.rows[0].id });

  } catch(e) {
    console.error(e);
    res.status(500).json({ success:false, error:e.message });
  }
});

app.post("/api/respond", async (req, res) => {
  try {
    const telegramId = req.body.telegram_id;
    const cargoId = Number(req.body.cargo_id);

    if (!telegramId || !cargoId) {
      return res.status(400).json({
        success: false,
        error: "telegram_id_and_cargo_id_required"
      });
    }

    const userResult = await pool.query(`
      SELECT id, full_name
      FROM users
      WHERE telegram_id=$1
      LIMIT 1
    `, [telegramId]);

    if (!userResult.rows.length) {
      return res.status(404).json({
        success: false,
        error: "user_not_found"
      });
    }

    const user = userResult.rows[0];

    const truckResult = await pool.query(`
      SELECT id
      FROM trucks
      WHERE driver_id=$1
      ORDER BY id DESC
      LIMIT 1
    `, [user.id]);

    if (!truckResult.rows.length) {
      return res.status(400).json({
        success: false,
        error: "truck_not_found"
      });
    }

    const truck = truckResult.rows[0];

    const existing = await pool.query(`
      SELECT id
      FROM responses
      WHERE cargo_id=$1 AND driver_id=$2
      LIMIT 1
    `, [cargoId, user.id]);

    if (existing.rows.length) {
      return res.json({
        success: true,
        already: true,
        response_id: existing.rows[0].id
      });
    }

    const inserted = await pool.query(`
      INSERT INTO responses (
        cargo_id,
        truck_id,
        driver_id,
        message,
        status
      )
      VALUES ($1,$2,$3,$4,'pending')
      RETURNING id
    `, [
      cargoId,
      truck.id,
      user.id,
      `Отклик с карты от ${user.full_name || "водителя"}`
    ]);

    const responseId = inserted.rows[0].id;

    const cargoOwner = await pool.query(`
      SELECT
        u.telegram_id,
        c.from_city,
        c.to_city
      FROM cargo c
      LEFT JOIN users u ON u.id = c.created_by
      WHERE c.id=$1
      LIMIT 1
    `, [cargoId]);

    if (cargoOwner.rows.length && cargoOwner.rows[0].telegram_id) {

      const owner = cargoOwner.rows[0];

      try {

        await fetch(
          `https://api.telegram.org/bot${BOT_TOKEN}/sendMessage`,
          {
            method:'POST',
            headers:{
              'Content-Type':'application/json'
            },
            body:JSON.stringify({
              chat_id: owner.telegram_id,
              text:
                `📨 Новый отклик с карты\n\n` +
                `🚚 Водитель: ${user.full_name || 'Водитель'}\n` +
                `📦 Груз #${cargoId}\n` +
                `🚩 ${owner.from_city} → ${owner.to_city}`,
              reply_markup:{
                inline_keyboard:[
                  [
                    {
                      text:'✅ Принять',
                      callback_data:`accept_${responseId}`
                    },
                    {
                      text:'❌ Отклонить',
                      callback_data:`reject_${responseId}`
                    }
                  ]
                ]
              }
            })
          }
        );

      } catch(e) {
        console.error("telegram notify error", e);
      }
    }

    res.json({
      success: true,
      response_id: responseId
    });

  } catch (e) {
    console.error(e);
    res.status(500).json({
      success: false,
      error: e.message
    });
  }
});

app.post("/api/cargo/offer", async (req, res) => {
  try {
    const { cargo_id, driver_id } = req.body;

    if (!cargo_id || !driver_id) {
      return res.status(400).json({ success:false, error:"cargo_id_driver_id_required" });
    }

    const cargoResult = await pool.query(`
      SELECT
        c.id,
        c.from_city,
        c.to_city,
        c.price_amount,
        c.price_currency,
        c.rate_per_km,
        c.created_by
      FROM cargo c
      WHERE c.id=$1
      LIMIT 1
    `, [cargo_id]);

    if (!cargoResult.rows.length) {
      return res.status(404).json({ success:false, error:"cargo_not_found" });
    }

    const truckResult = await pool.query(`
      SELECT
        t.id AS truck_id,
        t.driver_id,
        u.telegram_id,
        u.full_name
      FROM trucks t
      JOIN users u ON u.id = t.driver_id
      WHERE t.driver_id=$1
      ORDER BY t.id DESC
      LIMIT 1
    `, [driver_id]);

    if (!truckResult.rows.length) {
      return res.status(404).json({ success:false, error:"driver_truck_not_found" });
    }

    const cargo = cargoResult.rows[0];
    const driver = truckResult.rows[0];

    const existing = await pool.query(`
      SELECT id
      FROM responses
      WHERE cargo_id=$1 AND driver_id=$2
      LIMIT 1
    `, [cargo.id, driver.driver_id]);

    if (existing.rows.length) {
      return res.json({
        success:true,
        already:true,
        response_id: existing.rows[0].id
      });
    }

    const inserted = await pool.query(`
      INSERT INTO responses (
        cargo_id,
        truck_id,
        driver_id,
        message,
        status
      )
      VALUES ($1,$2,$3,$4,'offered')
      RETURNING id
    `, [
      cargo.id,
      driver.truck_id,
      driver.driver_id,
      "Предложение рейса от диспетчера"
    ]);

    const responseId = inserted.rows[0].id;

    if (driver.telegram_id) {
      try {
        await fetch(
          `https://api.telegram.org/bot${BOT_TOKEN}/sendMessage`,
          {
            method:"POST",
            headers:{ "Content-Type":"application/json" },
            body:JSON.stringify({
              chat_id: driver.telegram_id,
              text:
                `📨 Вам предложили рейс\n\n` +
                `📦 Груз #${cargo.id}\n` +
                `🚩 ${cargo.from_city} → ${cargo.to_city}\n` +
                `💰 ${cargo.price_amount || "-"} ${cargo.price_currency || "₽"}\n` +
                `💵 ${cargo.rate_per_km || "-"} ₽/км`,
              reply_markup:{
                inline_keyboard:[
                  [
                    { text:"✅ Принять", callback_data:`accept_${responseId}` },
                    { text:"❌ Отказаться", callback_data:`reject_${responseId}` }
                  ],
                  [
                    { text:"🗺 Открыть карту", web_app:{ url:"https://dalnoboybros.ru?v=138" } }
                  ]
                ]
              }
            })
          }
        );
      } catch(e) {
        console.error("telegram offer notify error", e);
      }
    }

    io.emit("cargo_offered", {
      cargo_id: cargo.id,
      driver_id: driver.driver_id,
      response_id: responseId
    });

    res.json({
      success:true,
      response_id: responseId
    });

  } catch(e) {
    console.error(e);
    res.status(500).json({ success:false, error:e.message });
  }
});



app.post("/api/realtime/response-status", async (req, res) => {
  try {
    const { response_id, status, cargo_id, truck_id, deal_id } = req.body;

    if (!response_id || !status) {
      return res.status(400).json({ success:false, error:"response_id_status_required" });
    }

    io.emit("response_status_updated", {
      response_id,
      status,
      cargo_id,
      truck_id,
      deal_id: deal_id || null,
      updated_at: new Date().toISOString()
    });

    res.json({ success:true });
  } catch(e) {
    console.error(e);
    res.status(500).json({ success:false, error:e.message });
  }
});



app.post("/api/realtime/deal-status", async (req, res) => {
  try {
    const { deal_id, cargo_id, status, status_text } = req.body;

    if (!deal_id || !status) {
      return res.status(400).json({ success:false, error:"deal_id_status_required" });
    }

    io.emit("deal_status_updated", {
      deal_id,
      cargo_id: cargo_id || null,
      status,
      status_text: status_text || status,
      updated_at: new Date().toISOString()
    });

    res.json({ success:true });
  } catch(e) {
    console.error(e);
    res.status(500).json({ success:false, error:e.message });
  }
});




app.get("/api/deals", async (req, res) => {
  try {
    const rows = await pool.query(`
      SELECT
        d.id,
        d.status,
        d.created_at,
        d.updated_at,
        d.cargo_id,
        d.truck_id,
        t.driver_id as truck_driver_id,
        u.id as driver_id,
        d.client_price,
        d.carrier_price,
        d.dispatcher_profit,
        t.photo_url as truck_photo_url,

        c.from_city,
        c.to_city,
        c.price_amount,
        c.price_currency,

        u.full_name as driver_name,
        ts.score as driver_trust_score

      FROM deals d

      LEFT JOIN cargo c
        ON c.id = d.cargo_id

      LEFT JOIN trucks t
        ON t.id = d.truck_id

      LEFT JOIN users u
        ON u.id = t.driver_id

      LEFT JOIN trust_scores ts
        ON ts.user_id = u.id

      ORDER BY d.id DESC
      LIMIT 200
    `);

    res.json({
      success: true,
      count: rows.rows.length,
      items: rows.rows
    });

  } catch (e) {
    console.error(e);
    res.status(500).json({
      success: false,
      error: e.message
    });
  }
});



app.get("/api/deal/:id", async (req, res) => {
  try {
    const dealId = Number(req.params.id);

    const deal = await pool.query(`
      SELECT
        d.*,
        c.from_city,
        c.to_city,
        c.price_amount,
        c.price_currency,
        u.full_name as driver_name,
        ts.score as driver_trust_score,
        t.photo_url as truck_photo_url,
        dc.note as dispatcher_client_note
      FROM deals d
      LEFT JOIN cargo c ON c.id=d.cargo_id
      LEFT JOIN trucks t ON t.id=d.truck_id
      LEFT JOIN users u ON u.id=t.driver_id
      LEFT JOIN trust_scores ts ON ts.user_id=u.id
      LEFT JOIN dispatcher_clients dc ON dc.client_user_id=t.driver_id
      WHERE d.id=$1
      LIMIT 1
    `, [dealId]);

    if (!deal.rows.length) {
      return res.status(404).json({ success:false, error:"deal_not_found" });
    }

    const history = await pool.query(`
      SELECT *
      FROM deal_status_history
      WHERE deal_id=$1
      ORDER BY created_at DESC
    `, [dealId]);

    res.json({
      success:true,
      deal: deal.rows[0],
      history: history.rows
    });

  } catch(e) {
    console.error(e);
    res.status(500).json({ success:false, error:e.message });
  }
});

app.post("/api/deal/:id/status", async (req, res) => {
  try {
    const dealId = Number(req.params.id);
    const { status, user_id } = req.body;

    if (!status) {
      return res.status(400).json({ success:false, error:"status_required" });
    }

    const oldDeal = await pool.query(`
      SELECT status
      FROM deals
      WHERE id=$1
      LIMIT 1
    `, [dealId]);

    if (!oldDeal.rows.length) {
      return res.status(404).json({ success:false, error:"deal_not_found" });
    }

    const oldStatus = oldDeal.rows[0].status;

    await pool.query(`
      UPDATE deals
      SET status=$1, updated_at=now()
      WHERE id=$2
    `, [status, dealId]);

    await pool.query(`
      INSERT INTO deal_status_history(deal_id, status, created_by)
      VALUES($1,$2,$3)
    `, [dealId, status, user_id || null]);

    await pool.query(`
      INSERT INTO deal_audit_log(
        deal_id,
        user_id,
        action,
        old_value,
        new_value,
        meta
      )
      VALUES($1,$2,$3,$4,$5,$6)
    `, [
      dealId,
      user_id || null,
      "deal_status_changed",
      oldStatus,
      status,
      JSON.stringify({ source: "dispatcher_web" })
    ]);

    io.emit("deal_status_updated", {
      deal_id: dealId,
      status
    });

    res.json({ success:true });

  } catch(e) {
    console.error(e);
    res.status(500).json({ success:false, error:e.message });
  }
});



app.post("/api/disputes", async (req, res) => {
  try {
    const { deal_id, opened_by, reason, description } = req.body;

    if (!deal_id || !reason) {
      return res.status(400).json({ success:false, error:"deal_id_reason_required" });
    }

    const row = await pool.query(`
      INSERT INTO disputes(deal_id, opened_by, reason, description)
      VALUES($1,$2,$3,$4)
      RETURNING *
    `, [deal_id, opened_by || null, reason, description || null]);

    await pool.query(`
      UPDATE deals
      SET dispute=true, updated_at=now()
      WHERE id=$1
    `, [deal_id]);

    await pool.query(`
      INSERT INTO deal_audit_log(deal_id, user_id, action, new_value, meta)
      VALUES($1,$2,$3,$4,$5)
    `, [
      deal_id,
      opened_by || null,
      "dispute_opened",
      reason,
      JSON.stringify({ description: description || "" })
    ]);

    res.json({ success:true, dispute: row.rows[0] });

  } catch(e) {
    console.error(e);
    res.status(500).json({ success:false, error:e.message });
  }
});

app.get("/api/disputes", async (req, res) => {
  try {
    const rows = await pool.query(`
      SELECT
        ds.*,
        d.status AS deal_status,
        c.from_city,
        c.to_city,
        u.full_name AS opened_by_name
      FROM disputes ds
      LEFT JOIN deals d ON d.id=ds.deal_id
      LEFT JOIN cargo c ON c.id=d.cargo_id
      LEFT JOIN users u ON u.id=ds.opened_by
      ORDER BY ds.id DESC
      LIMIT 200
    `);

    res.json({ success:true, count:rows.rows.length, items:rows.rows });

  } catch(e) {
    console.error(e);
    res.status(500).json({ success:false, error:e.message });
  }
});



app.get("/api/deal/:id/audit", async (req, res) => {
  try {
    const dealId = Number(req.params.id);

    const rows = await pool.query(`
      SELECT
        a.id,
        a.deal_id,
        a.user_id,
        a.action,
        a.old_value,
        a.new_value,
        a.meta,
        a.created_at,
        u.full_name
      FROM deal_audit_log a
      LEFT JOIN users u
        ON u.id = a.user_id
      WHERE a.deal_id=$1
      ORDER BY a.created_at DESC
      LIMIT 100
    `, [dealId]);

    res.json({
      success: true,
      count: rows.rows.length,
      items: rows.rows
    });

  } catch(e) {
    console.error(e);
    res.status(500).json({
      success: false,
      error: e.message
    });
  }
});



app.get("/api/deal/:id/documents", async (req, res) => {
  try {
    const dealId = Number(req.params.id);

    const rows = await pool.query(`
      SELECT
        d.id,
        d.deal_id,
        d.uploaded_by,
        d.doc_type,
        d.file_name,
        d.caption,
        d.created_at,
        u.full_name AS uploaded_by_name
      FROM deal_documents d
      LEFT JOIN users u
        ON u.id = d.uploaded_by
      WHERE d.deal_id=$1
      ORDER BY d.id DESC
    `, [dealId]);

    res.json({
      success: true,
      count: rows.rows.length,
      items: rows.rows
    });

  } catch(e) {
    console.error(e);
    res.status(500).json({
      success: false,
      error: e.message
    });
  }
});



app.get("/api/dispatcher/clients", async (req, res) => {
  try {

    const rows = await pool.query(`
      SELECT
        dc.id,
        dc.dispatcher_user_id,
        dc.client_user_id,
        dc.client_type,
        dc.commission_percent,
        dc.status,
        dc.note,

        u.full_name,
        u.role,
        u.verified,
        ts.score AS trust_score

      FROM dispatcher_clients dc

      LEFT JOIN users u
        ON u.id = dc.client_user_id

      LEFT JOIN trust_scores ts
        ON ts.user_id = u.id

      ORDER BY dc.id DESC
    `);

    res.json({
      success:true,
      count:rows.rows.length,
      items:rows.rows
    });

  } catch(e) {
    console.error(e);
    res.status(500).json({
      success:false,
      error:e.message
    });
  }
});



app.post("/api/dispatcher/client/:id/note", async (req, res) => {
  try {
    const id = Number(req.params.id);
    const { note } = req.body;

    const row = await pool.query(`
      UPDATE dispatcher_clients
      SET note=$1
      WHERE id=$2
      RETURNING id, note
    `, [note || null, id]);

    if (!row.rows.length) {
      return res.status(404).json({
        success:false,
        error:"client_not_found"
      });
    }

    res.json({
      success:true,
      client:row.rows[0]
    });

  } catch(e) {
    console.error(e);
    res.status(500).json({
      success:false,
      error:e.message
    });
  }
});


app.get("/", (req, res) => {
  res.sendFile("/root/dalnoboy/web/map.html");
});

app.get("/map", (req, res) => {
  res.sendFile("/root/dalnoboy/web/map.html");
});



// === TELEGRAM_SOCKET_AUTH_V1 ===
io.use((socket, next) => {
  try {
    const initData =
      (socket.handshake &&
       socket.handshake.auth &&
       socket.handshake.auth.initData) ||
      (socket.handshake &&
       socket.handshake.query &&
       socket.handshake.query.initData) ||
      "";

    const verified = verifyTelegramInitData(initData);

    if (!verified.ok) {
      const err = new Error("telegram_webapp_auth_required");
      err.data = { code: "telegram_webapp_auth_required" };
      return next(err);
    }

    socket.telegramWebAppUser = verified.user || null;
    return next();
  } catch (e) {
    const err = new Error("telegram_socket_auth_error");
    err.data = { code: "telegram_socket_auth_error" };
    return next(err);
  }
});
// === END TELEGRAM_SOCKET_AUTH_V1 ===

io.on("connection", (socket) => {
  console.log("Socket connected:", socket.id);
});

server.listen(5000, "0.0.0.0", () => {
  console.log("Server started: http://0.0.0.0:5000");
});
