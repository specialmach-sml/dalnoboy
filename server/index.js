require("dotenv").config({ path: "../.env" });

const express = require("express");
const cors = require("cors");
const { Pool } = require("pg");

const app = express();
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
        u.full_name
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
        load_longitude
      FROM cargo
      WHERE status='open'
        AND load_latitude IS NOT NULL
        AND load_longitude IS NOT NULL
      ORDER BY id DESC
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
        load_longitude
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
      distance_km
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
        status
      )
      VALUES (
        $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,'open'
      )
      RETURNING id, rate_per_km
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
      rate
    ]);

    res.json({
      success: true,
      cargo_id: q.rows[0].id,
      rate_per_km: q.rows[0].rate_per_km
    });

  } catch(e) {
    console.error(e);
    res.status(500).json({ success:false, error:"server_error" });
  }
});


app.listen(5000, () => {
  console.log("Server started: http://localhost:5000");
});