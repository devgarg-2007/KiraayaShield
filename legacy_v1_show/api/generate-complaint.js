// Vercel Serverless Function: /api/generate-complaint
export default function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const body = req.body || {};
  const ticketId = "NCH-2026-" + Math.floor(100000 + Math.random() * 900000);

  return res.status(200).json({
    status: "success",
    ticketId,
    channel: "National Consumer Helpline (INGRAM) & e-Daakhil Ready",
    generatedAt: new Date().toISOString(),
    legalStatutesCited: [
      "Section 11, Model Tenancy Act 2021 (Security Deposit Cap & Same-Day Refund)",
      "Section 73 & 74, Indian Contract Act 1872 (Compensation for Breach & Unlawful Penalty)",
      "Section 2(11) & 2(47), Consumer Protection Act 2019 (Deficiency in Service & Unfair Trade Practice)"
    ],
    interestClaimed: "18% per annum from date of vacating possession",
    curePeriodDays: 15,
    payloadEcho: body
  });
}
