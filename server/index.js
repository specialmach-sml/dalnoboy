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
      volume_m3
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
        created_by,
        status
      )
      VALUES (
        $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,'open'
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
      Number(weight_tons || 0) || null,
      Number(volume_m3 || 0) || null,
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
            + CASE WHEN COALESCE(t.capacity_tons, 0) >= COALESCE($1, 0) THEN 15 ELSE 0 END
            + CASE WHEN COALESCE(t.volume_m3, 0) >= COALESCE($2, 0) THEN 15 ELSE 0 END
            + CASE WHEN COALESCE(u.plan_type, 'free') IN ('pro','company') THEN 10 ELSE 0 END
            + CASE
                WHEN COALESCE($5, 0) > 0
                 AND COALESCE(t.min_rate_per_km, 0) > 0
                 AND COALESCE($5, 0) >= COALESCE(t.min_rate_per_km, 0)
                THEN 20
                WHEN COALESCE($5, 0) > 0
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
        ts.score as driver_trust_score
      FROM deals d
      LEFT JOIN cargo c ON c.id=d.cargo_id
      LEFT JOIN trucks t ON t.id=d.truck_id
      LEFT JOIN users u ON u.id=t.driver_id
      LEFT JOIN trust_scores ts ON ts.user_id=u.id
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