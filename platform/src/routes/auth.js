'use strict';

const express = require('express');
const db = require('../db');
const { verifyPassword, signToken } = require('../auth');

const router = express.Router();

router.post('/login', (req, res) => {
  const { email, password } = req.body || {};
  if (!email || !password) return res.status(400).json({ error: 'email and password required' });

  const user = db.prepare('SELECT * FROM users WHERE email = ?').get(email);
  if (!user || !verifyPassword(password, user.salt, user.password_hash)) {
    return res.status(401).json({ error: 'invalid credentials' });
  }

  const token = signToken({ sub: user.id, email: user.email, role: user.role, tenant_id: user.tenant_id });
  res.json({
    token,
    user: { id: user.id, email: user.email, role: user.role, tenant_id: user.tenant_id },
  });
});

module.exports = router;
