// vertical.js — the browser side of verticals.py: terminology and feature
// flags for the business's profession. The server sends the active vertical's
// config (verticals/<id>.json) and the owner's gender with /api/auth/me;
// setVertical() installs it, then:
//   t('client')            → "תלמיד" / "לקוח"
//   t('reminder_one', {name, time})
//   has('exams')           → feature flag
//   g('הוסיפי', 'הוסף')    → the form matching the owner's gender
// No profession-specific logic belongs anywhere else in the UI.
(function (root) {
  'use strict';
  const state = { vertical: null, gender: 'f' };

  function setVertical(config, gender) {
    state.vertical = config || null;
    state.gender = gender === 'm' ? 'm' : 'f';
  }

  function pick(val) {
    if (val && typeof val === 'object') return val[state.gender] || val.f || Object.values(val)[0];
    return val;
  }

  function t(key, params) {
    const terms = (state.vertical && state.vertical.terms) || {};
    let val = Object.prototype.hasOwnProperty.call(terms, key) ? pick(terms[key]) : key;
    if (params) for (const [k, v] of Object.entries(params)) val = val.split('{' + k + '}').join(String(v));
    return val;
  }

  function has(feature) {
    return !!(state.vertical && state.vertical.features && state.vertical.features[feature]);
  }

  function g(feminine, masculine) {
    return state.gender === 'm' ? masculine : feminine;
  }

  function vertical() { return state.vertical; }
  function gender() { return state.gender; }

  const api = { setVertical, t, has, g, vertical, gender };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else Object.assign(root, api);
})(typeof window !== 'undefined' ? window : globalThis);
