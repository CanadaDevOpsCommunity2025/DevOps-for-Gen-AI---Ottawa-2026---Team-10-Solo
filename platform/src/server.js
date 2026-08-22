'use strict';

const express = require('express');
const path = require('node:path');

const authRoutes = require('./routes/auth');
const appsRoutes = require('./routes/apps');
const ingestRoutes = require('./routes/ingest');
const dashboardRoutes = require('./routes/dashboard');

const PORT = process.env.PORT || 4000;

const app = express();
app.use(express.json({ limit: '1mb' }));

// Minimal CORS: any AI-agent-built app (e.g. flight-recorder's bridge, or a
// future @sentinel/observe-sdk consumer) runs on a different origin/port and
// needs to call the ingest endpoints directly, not from a browser — but we
// still allow cross-origin so a browser-based caller could reach it too.
app.use((req, res, next) => {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Authorization, x-app-id, x-ingest-key');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS');
  if (req.method === 'OPTIONS') return res.sendStatus(204);
  next();
});

app.use('/api/auth', authRoutes);
app.use('/api/apps', appsRoutes);
app.use('/api/ingest', ingestRoutes);
app.use('/api', dashboardRoutes);

app.use(express.static(path.join(__dirname, '..', 'public')));

app.get('/health', (req, res) => res.json({ ok: true }));

app.listen(PORT, () => {
  // eslint-disable-next-line no-console
  console.log(`Sentinel platform listening on http://localhost:${PORT}`);
});
