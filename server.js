const express = require('express');
const cors = require('cors');
const path = require('path');

const app = express();
const PORT = process.env.PORT || 3000;

app.use(cors());
app.use(express.json({ limit: '10mb' }));
app.use(express.static(path.join(__dirname)));

app.post('/api/analyze-lease', (req, res) => {
  const { city = "Noida", monthlyRent = 18000, depositPaid = 54000 } = req.body || {};
  res.json({
    status: "success",
    engine: "KiraayaShield-LegalNLP-v2.4 (Express Local)",
    city,
    riskScore: 86,
    recoverableEstimate: Math.round(depositPaid * 0.68)
  });
});

app.get('/api/scrape-rates', (req, res) => {
  res.json({
    status: "success",
    scrapedAt: new Date().toISOString(),
    sourcesQueried: ["Urban Company", "NoBroker", "Justdial"]
  });
});

app.post('/api/generate-complaint', (req, res) => {
  res.json({
    status: "success",
    ticketId: "NCH-2026-" + Math.floor(100000 + Math.random() * 900000),
    generatedAt: new Date().toISOString()
  });
});

app.get('*', (req, res) => {
  res.sendFile(path.join(__dirname, 'index.html'));
});

app.listen(PORT, () => {
  console.log(`\n🛡️  KiraayaShield Pro Server running at http://localhost:${PORT}\n`);
});
