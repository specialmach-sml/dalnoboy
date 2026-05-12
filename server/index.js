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

app.listen(5000, () => {
  console.log("Server started: http://localhost:5000");
});