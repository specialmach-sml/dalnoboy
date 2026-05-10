const express = require("express");
const cors = require("cors");

const app = express();

app.use(cors());
app.use(express.json());

let orders = [
  {
    id: 1,
    from: "Берлин",
    to: "Гамбург",
    cargo: "Автозапчасти",
    weight: 12,
    price: 850,
  },
  {
    id: 2,
    from: "Франкфурт",
    to: "Мюнхен",
    cargo: "Продукты",
    weight: 20,
    price: 1200,
  },
];

app.get("/orders", (req, res) => {
  res.json(orders);
});

app.post("/orders", (req, res) => {
  const newOrder = {
    id: Date.now(),
    ...req.body,
  };

  orders.push(newOrder);

  res.json(newOrder);
});

app.delete("/orders/:id", (req, res) => {
  const id = Number(req.params.id);

  orders = orders.filter((o) => o.id !== id);

  res.json({ success: true });
});

app.listen(5000, () => {
  console.log("Server started: http://localhost:5000");
});