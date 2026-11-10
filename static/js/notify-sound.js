/* Notification tones, made in the browser (no sound files). window.VistSound.play(tone, {volume, repeat}) and .preview().
   Tones: 1 soft chime, 2 bell, 3 marimba, 4 digital beep, 5 phone ring (long), 6 melody (long), 7 alarm (long, for urgent).
   Browsers only allow sound after the person has clicked or typed on the page once; until then a tone is skipped quietly. */
(function () {
  "use strict";
  let ctx = null;
  function audio() {
    if (!ctx) {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return null;
      ctx = new AC();
    }
    if (ctx.state === "suspended") ctx.resume().catch(function () {});
    return ctx;
  }
  // unlock on the first click / key (a browser rule), so later notifications can play
  ["pointerdown", "keydown"].forEach(function (ev) {
    window.addEventListener(ev, function () { try { audio(); } catch (e) { /* no audio */ } }, { once: true, passive: true });
  });

  function note(c, out, t, freq, dur, opts) {
    const o = c.createOscillator(), g = c.createGain();
    o.type = (opts && opts.type) || "sine";
    o.frequency.setValueAtTime(freq, t);
    if (opts && opts.slide) o.frequency.exponentialRampToValueAtTime(opts.slide, t + dur);
    const peak = (opts && opts.gain) || 0.6, attack = (opts && opts.attack) || 0.008;
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(peak, t + attack);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    o.connect(g); g.connect(out);
    o.start(t); o.stop(t + dur + 0.05);
    if (opts && opts.harmonic) {            // a bell-like overtone
      const o2 = c.createOscillator(), g2 = c.createGain();
      o2.frequency.setValueAtTime(freq * opts.harmonic, t);
      g2.gain.setValueAtTime(0.0001, t);
      g2.gain.exponentialRampToValueAtTime(peak * 0.35, t + attack);
      g2.gain.exponentialRampToValueAtTime(0.0001, t + dur * 0.6);
      o2.connect(g2); g2.connect(out); o2.start(t); o2.stop(t + dur);
    }
  }

  // each returns how long it lasts (seconds)
  const TONES = {
    1: function (c, o, t) { note(c, o, t, 880, 0.5, { gain: 0.5 }); note(c, o, t + 0.16, 1318.5, 0.7, { gain: 0.45 }); return 0.9; },
    2: function (c, o, t) { note(c, o, t, 659.3, 1.6, { gain: 0.6, harmonic: 2.76, attack: 0.004 }); return 1.6; },
    3: function (c, o, t) { [523.3, 659.3, 784, 1046.5].forEach(function (f, i) { note(c, o, t + i * 0.12, f, 0.35, { gain: 0.5, type: "triangle" }); }); return 0.85; },
    4: function (c, o, t) { [0, 0.18].forEach(function (d) { note(c, o, t + d, 1500, 0.11, { gain: 0.25, type: "square", attack: 0.002 }); }); return 0.4; },
    5: function (c, o, t) {                 // a classic phone ring: two bursts, twice (about 5 seconds)
      for (let r = 0; r < 2; r++) {
        for (let b = 0; b < 2; b++) {
          const s = t + r * 2.6 + b * 0.55;
          for (let k = 0; k < 8; k++) note(c, o, s + k * 0.05, k % 2 ? 480 : 440, 0.05, { gain: 0.3, type: "triangle", attack: 0.003 });
        }
      }
      return 5.0;
    },
    6: function (c, o, t) {                 // a short melody, like a phone ringtone (about 4 seconds)
      const seq = [[659.3, 0.25], [784, 0.25], [880, 0.5], [784, 0.25], [659.3, 0.25], [587.3, 0.5], [659.3, 0.25], [784, 0.25], [987.8, 0.5], [880, 0.9]];
      let s = t;
      seq.forEach(function (n) { note(c, o, s, n[0], n[1] * 0.95, { gain: 0.45, type: "triangle" }); s += n[1]; });
      return s - t;
    },
    7: function (c, o, t) {                 // alarm for urgent notices (about 4 seconds)
      for (let k = 0; k < 8; k++) note(c, o, t + k * 0.5, 880, 0.42, { gain: 0.45, type: "sawtooth", slide: 1320, attack: 0.01 });
      return 4.0;
    },
  };

  function play(tone, opts) {
    tone = Number(tone);
    const make = TONES[tone];
    if (!make) return false;
    let c;
    try { c = audio(); } catch (e) { return false; }
    if (!c || c.state !== "running") return false;          // not unlocked by a click yet
    const vol = Math.max(0, Math.min(100, Number(opts && opts.volume != null ? opts.volume : 70))) / 100;
    const master = c.createGain();
    master.gain.value = vol * vol;                            // feels linear to the ear
    master.connect(c.destination);
    let t = c.currentTime + 0.03;
    const times = Math.max(1, Math.min(5, Number((opts && opts.repeat) || 1)));
    for (let i = 0; i < times; i++) { t += make(c, master, t) + 0.35; }
    return true;
  }

  window.VistSound = { play: play, preview: function (tone, opts) { audio(); setTimeout(function () { play(tone, opts); }, 60); }, tones: Object.keys(TONES).map(Number) };
})();
