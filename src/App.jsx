import { useEffect, useState } from "react";
import "./App.css";

function App() {
  const [tab, setTab] = useState("orders");
  const [orders, setOrders] = useState([]);

  const [form, setForm] = useState({
    from: "",
    to: "",
    cargo: "",
    weight: "",
    price: "",
  });

  const loadOrders = () => {
    fetch("http://localhost:5000/orders")
      .then((res) => res.json())
      .then((data) => setOrders(data))
      .catch((err) => console.error(err));
  };

  useEffect(() => {
    loadOrders();
  }, []);

  const handleSubmit = async (e) => {
    e.preventDefault();

    const res = await fetch("http://localhost:5000/orders", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(form),
    });

    const newOrder = await res.json();

    setOrders([...orders, newOrder]);

    setForm({
      from: "",
      to: "",
      cargo: "",
      weight: "",
      price: "",
    });

    setTab("orders");
  };

  const deleteOrder = async (id) => {
    await fetch(`http://localhost:5000/orders/${id}`, {
      method: "DELETE",
    });

    setOrders(orders.filter((o) => o.id !== id));
  };

  return (
    <div className="screen">
      {tab === "orders" && (
        <>
          <h1>Заявки</h1>

          <button className="refresh-btn" onClick={loadOrders}>
            Обновить
          </button>

          {orders.map((o) => (
            <div className="card" key={o.id}>
              <button
                className="delete-btn"
                onClick={() => deleteOrder(o.id)}
              >
                ❌
              </button>

              <b>
                {o.from} → {o.to}
              </b>

              <div>Груз: {o.cargo}</div>
              <div>Вес: {o.weight} т</div>
              <div className="price">€{o.price}</div>
            </div>
          ))}
        </>
      )}

      {tab === "add" && (
        <>
          <h1>Новая заявка</h1>

          <form onSubmit={handleSubmit} className="form">
            <input
              placeholder="Откуда"
              value={form.from}
              onChange={(e) =>
                setForm({ ...form, from: e.target.value })
              }
            />

            <input
              placeholder="Куда"
              value={form.to}
              onChange={(e) =>
                setForm({ ...form, to: e.target.value })
              }
            />

            <input
              placeholder="Груз"
              value={form.cargo}
              onChange={(e) =>
                setForm({ ...form, cargo: e.target.value })
              }
            />

            <input
              placeholder="Вес (тонн)"
              value={form.weight}
              onChange={(e) =>
                setForm({ ...form, weight: e.target.value })
              }
            />

            <input
              placeholder="Цена (€)"
              value={form.price}
              onChange={(e) =>
                setForm({ ...form, price: e.target.value })
              }
            />

            <button type="submit">Создать</button>
          </form>
        </>
      )}

      {tab === "deals" && <h1>Мои рейсы</h1>}
      {tab === "handshake" && <h1>Сделки</h1>}
      {tab === "profile" && <h1>Профиль</h1>}

      <nav className="bottom-nav">
        <button onClick={() => setTab("orders")}>📦</button>
        <button onClick={() => setTab("deals")}>🚚</button>
        <button onClick={() => setTab("add")}>➕</button>
        <button onClick={() => setTab("handshake")}>🤝</button>
        <button onClick={() => setTab("profile")}>👤</button>
      </nav>
    </div>
  );
}

export default App;