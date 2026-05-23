require("dotenv").config({ path: "../.env" });
const { Pool } = require("pg");

const pool = new Pool({
  connectionString: process.env.DATABASE_URL
});

async function geocodeCity(city) {
  const url =
    "https://nominatim.openstreetmap.org/search?format=json&limit=1&q=" +
    encodeURIComponent(city + ", Russia");

  const res = await fetch(url, {
    headers: {
      "User-Agent": "DalnoboyBot/1.0"
    }
  });

  const data = await res.json();

  if (!data.length) return null;

  return {
    lat: Number(data[0].lat),
    lon: Number(data[0].lon)
  };
}

async function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function main() {
  const rows = await pool.query(`
    SELECT id, from_city, to_city
    FROM cargo
    WHERE status='open'
      AND (
        load_latitude IS NULL
        OR load_longitude IS NULL
        OR unload_latitude IS NULL
        OR unload_longitude IS NULL
      )
    ORDER BY id DESC
    LIMIT 20
  `);

  console.log("Need geocode:", rows.rows.length);

  for (const c of rows.rows) {
    console.log("Cargo", c.id, c.from_city, "->", c.to_city);

    const from = await geocodeCity(c.from_city);
    await sleep(1200);

    const to = await geocodeCity(c.to_city);
    await sleep(1200);

    if (!from || !to) {
      console.log("Skip: geocode failed");
      continue;
    }

    await pool.query(`
      UPDATE cargo
      SET
        load_latitude=$1,
        load_longitude=$2,
        unload_latitude=$3,
        unload_longitude=$4
      WHERE id=$5
    `, [from.lat, from.lon, to.lat, to.lon, c.id]);

    console.log("Updated:", c.id, from, to);
  }

  await pool.end();
}

main().catch(async e => {
  console.error(e);
  await pool.end();
});
