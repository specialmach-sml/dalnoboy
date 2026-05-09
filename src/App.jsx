function App() {
  const orders = [
    {
      id: 1,
      route: "Берлин → Гамбург",
      cargo: "Автозапчасти",
      price: "€850",
      date: "10.05.2026",
    },
    {
      id: 2,
      route: "Франкфурт → Мюнхен",
      cargo: "Продукты",
      price: "€1200",
      date: "11.05.2026",
    },
    {
      id: 3,
      route: "Кёльн → Дрезден",
      cargo: "Стройматериалы",
      price: "€970",
      date: "12.05.2026",
    },
  ];

  return (
    <div
      style={{
        background: "#111827",
        minHeight: "100vh",
        color: "white",
        padding: "20px",
        fontFamily: "Arial",
      }}
    >
      <h1 style={{ marginBottom: "20px" }}>Заявки</h1>

      {orders.map((order) => (
        <div
          key={order.id}
          style={{
            background: "#1f2937",
            padding: "20px",
            borderRadius: "14px",
            marginBottom: "15px",
          }}
        >
          <h2>{order.route}</h2>
          <p>Груз: {order.cargo}</p>
          <p>Дата: {order.date}</p>
          <h3 style={{ color: "#22c55e" }}>{order.price}</h3>
        </div>
      ))}
    </div>
  );
}

export default App;