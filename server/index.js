require("dotenv").config({ path: "../.env" });

const BOT_TOKEN = process.env.BOT_TOKEN;

const express = require("express");
const http = require("http");
const cors = require("cors");
const { Pool } = require("pg");
const { Server } = require("socket.io");

const app = express();
const server = http.createServer(app);
const io = new Server(server, { cors: { origin: "*" } });
app.use(cors());
app.use(express.json());

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
        AND t.location_updated_at > now() - interval '24 hours'
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
    const radius = Number(req.query.radius || 50);
    const profitabilityFilter = req.query.profitability || null;

    if (!telegramId) {
      return res.status(400).json({
        success: false,
        error: "telegram_id required"
      });
    }

    const truckResult = await pool.query(
      `
      SELECT t.latitude, t.longitude, t.min_rate_per_km
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





app.post("/api/cargo/create", async (req, res) => {
  try {
    const {
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
      volume_m3
    } = req.body;

    const price = Number(price_amount || 0);
    const dist = Number(distance_km || 0);
    const rate = dist > 0 ? Math.round((price / dist) * 100) / 100 : null;

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
        volume_m3,
        status
      )
      VALUES (
        $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,'open'
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
        volume_m3,
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
      Number(weight_tons || 0) || null,
      Number(volume_m3 || 0) || null
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
          + CASE WHEN COALESCE(t.available_tons, 0) >= COALESCE($1, 0) THEN 20 ELSE 0 END
          + CASE WHEN COALESCE(t.available_volume_m3, 0) >= COALESCE($2, 0) THEN 20 ELSE 0 END
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
          + CASE WHEN COALESCE(t.available_tons, 0) >= COALESCE($1, 0) THEN 20 ELSE 0 END
          + CASE WHEN COALESCE(t.available_volume_m3, 0) >= COALESCE($2, 0) THEN 20 ELSE 0 END
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
        (
          40
          + CASE WHEN COALESCE(t.capacity_tons, 0) >= COALESCE($1, 0) THEN 20 ELSE 0 END
          + CASE WHEN COALESCE(t.volume_m3, 0) >= COALESCE($2, 0) THEN 20 ELSE 0 END
          + CASE WHEN t.location_updated_at > now() - interval '2 hours' THEN 10 ELSE 0 END
          + CASE WHEN COALESCE(u.plan_type, 'free') IN ('pro','company') THEN 10 ELSE 0 END
        ) AS match_score
      FROM trucks t
      LEFT JOIN users u ON u.id = t.driver_id
      WHERE t.status='active'
        AND t.latitude IS NOT NULL
        AND t.longitude IS NOT NULL
        AND t.location_updated_at > now() - interval '24 hours'
      ORDER BY distance_km ASC, match_score DESC
      LIMIT 50
    `, [
      c.weight_tons,
      c.volume_m3,
      c.load_latitude,
      c.load_longitude
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

app.get("/", (req, res) => {
  res.sendFile("/root/dalnoboy/web/map.html");
});

app.get("/map", (req, res) => {
  res.sendFile("/root/dalnoboy/web/map.html");
});

io.on("connection", (socket) => {
  console.log("Socket connected:", socket.id);
});

server.listen(5000, "0.0.0.0", () => {
  console.log("Server started: http://0.0.0.0:5000");
});