'use strict';

const crypto = require('node:crypto');

if (process.env.NODE_ENV === 'production' && !process.env.SENTINEL_JWT_SECRET) {
  throw new Error(
    'SENTINEL_JWT_SECRET is required when NODE_ENV=production. Generate one with: ' +
    "node -e \"console.log(require('crypto').randomBytes(48).toString('hex'))\""
  );
}
if (!process.env.SENTINEL_JWT_SECRET) {
  // eslint-disable-next-line no-console
  console.warn('[auth] SENTINEL_JWT_SECRET not set — using an insecure dev-only default. ' +
    'Do not run like this outside local development.');
}
const JWT_SECRET = process.env.SENTINEL_JWT_SECRET || 'dev-only-secret-change-me';
const TOKEN_TTL_SECONDS = 8 * 60 * 60; // 8h

function base64url(input) {
  return Buffer.from(input).toString('base64url');
}

function hashPassword(password, salt = crypto.randomBytes(16).toString('hex')) {
  const hash = crypto.scryptSync(password, salt, 64).toString('hex');
  return { hash, salt };
}

function verifyPassword(password, salt, expectedHash) {
  const { hash } = hashPassword(password, salt);
  return crypto.timingSafeEqual(Buffer.from(hash, 'hex'), Buffer.from(expectedHash, 'hex'));
}

function signToken(payload) {
  const header = { alg: 'HS256', typ: 'JWT' };
  const now = Math.floor(Date.now() / 1000);
  const body = { ...payload, iat: now, exp: now + TOKEN_TTL_SECONDS };
  const headerPart = base64url(JSON.stringify(header));
  const bodyPart = base64url(JSON.stringify(body));
  const signature = crypto
    .createHmac('sha256', JWT_SECRET)
    .update(`${headerPart}.${bodyPart}`)
    .digest('base64url');
  return `${headerPart}.${bodyPart}.${signature}`;
}

function verifyToken(token) {
  const parts = String(token || '').split('.');
  if (parts.length !== 3) throw new Error('malformed token');
  const [headerPart, bodyPart, signature] = parts;
  const expected = crypto
    .createHmac('sha256', JWT_SECRET)
    .update(`${headerPart}.${bodyPart}`)
    .digest('base64url');
  if (!crypto.timingSafeEqual(Buffer.from(signature), Buffer.from(expected))) {
    throw new Error('invalid signature');
  }
  const body = JSON.parse(Buffer.from(bodyPart, 'base64url').toString('utf8'));
  if (body.exp && body.exp < Math.floor(Date.now() / 1000)) {
    throw new Error('token expired');
  }
  return body;
}

module.exports = { hashPassword, verifyPassword, signToken, verifyToken };
