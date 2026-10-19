"""Synthesizes an original upbeat pop bed plus SFX (whoosh on cuts, notification ding).
   python3 music.py out.wav DURATION "cut1,cut2,..." [DING_TIME|-] [KEY_SHIFT] [STYLE] [BPM]
   KEY_SHIFT: semitones (-5..6); STYLE: chord progression 0-3 — vary them so reels don't all sound alike."""
import sys, wave
import numpy as np

SR = 44100
out, DUR = sys.argv[1], float(sys.argv[2])
CUTS = [float(x) for x in sys.argv[3].split(',')] if len(sys.argv) > 3 else []
DING = float(sys.argv[4]) if len(sys.argv) > 4 and sys.argv[4] not in ('', '-') else None
KEY = int(sys.argv[5]) if len(sys.argv) > 5 else 0
STYLE = int(sys.argv[6]) % 4 if len(sys.argv) > 6 else 0
BPM = float(sys.argv[7]) if len(sys.argv) > 7 else 120.0
BEAT = 60 / BPM
N = int(DUR * SR)
t_all = np.arange(N) / SR
rng = np.random.default_rng(7 + STYLE)


def note(m):  # MIDI → Hz
    return 440.0 * 2 ** ((m - 69) / 12)


def lowpass(x, cutoff):
    # one-pole, vectorized via cumulative trick is unstable; do simple blockwise IIR in chunks
    a = np.exp(-2 * np.pi * cutoff / SR)
    y = np.empty_like(x)
    acc = 0.0
    for i in range(0, len(x), 4096):
        seg = x[i:i + 4096]
        out = np.empty_like(seg)
        for j, v in enumerate(seg):
            acc = (1 - a) * v + a * acc
            out[j] = acc
        y[i:i + 4096] = out
    return y


def env(n, attack, decay):
    t = np.arange(n) / SR
    e = np.minimum(1, t / max(attack, 1e-4)) * np.exp(-t / decay)
    return e


def place(buf, sig, start):
    i = int(start * SR)
    if i >= len(buf):
        return
    j = min(len(buf), i + len(sig))
    buf[i:j] += sig[:j - i]


def saw(freq, n, detune=0.0):
    t = np.arange(n) / SR
    f = freq * (1 + detune)
    return 2 * ((t * f) % 1.0) - 1


# Am – F – C – G (vi IV I V in C), one chord per bar (4 beats = 2 s)
PROGRESSIONS = [
    ([(57, 60, 64), (53, 57, 60), (48, 52, 55), (55, 59, 62)], [45, 41, 48, 43]),  # Am F C G
    ([(48, 52, 55), (55, 59, 62), (57, 60, 64), (53, 57, 60)], [48, 43, 45, 41]),  # C G Am F
    ([(53, 57, 60), (55, 59, 62), (52, 55, 59), (57, 60, 64)], [41, 43, 40, 45]),  # F G Em Am
    ([(50, 53, 57), (55, 59, 62), (48, 52, 55), (57, 60, 64)], [38, 43, 48, 45]),  # Dm G C Am
]
CHORDS = [tuple(m + KEY for m in c) for c in PROGRESSIONS[STYLE][0]]
BASS = [m + KEY for m in PROGRESSIONS[STYLE][1]]
bars = int(np.ceil(DUR / (4 * BEAT)))
DROP = min(CUTS[0], 3.0) if CUTS else 2.0  # beat drops on the first cut, after the hook
END = DUR - 2.5  # last bars: drums out, chord rings

pad = np.zeros(N); bass = np.zeros(N); pluck = np.zeros(N); drums = np.zeros(N); sfx = np.zeros(N)
kick_env = np.zeros(N)

for b in range(bars):
    t0 = b * 4 * BEAT
    ch = CHORDS[b % 4]
    # pad: detuned saws, whole bar
    n = int(4 * BEAT * SR)
    sig = sum(saw(note(m), n, d) for m in ch for d in (-0.004, 0.004)) / 6
    e = np.minimum(1, np.arange(n) / (0.08 * SR)) * np.minimum(1, (n - np.arange(n)) / (0.05 * SR))
    place(pad, sig * e, t0)
    if t0 + 0.01 < DROP:
        continue
    # bass: eighth notes on the root, octave pops on the offbeat 8ths
    for k in range(8):
        tk = t0 + k * BEAT / 2
        if tk >= END + 1.5:
            break
        m = BASS[b % 4] - 12 + (12 if k % 2 else 0)
        nn = int(BEAT / 2 * SR * 0.9)
        tt = np.arange(nn) / SR
        s = (np.sin(2 * np.pi * note(m) * tt) + 0.35 * np.sign(np.sin(2 * np.pi * note(m) * tt))) * env(nn, 0.004, 0.18)
        place(bass, s, tk)
    # pluck arpeggio: 16ths up the chord
    arp = [ch[0] + 12, ch[1] + 12, ch[2] + 12, ch[1] + 24, ch[2] + 12, ch[0] + 24, ch[1] + 12, ch[2] + 24]
    for k in range(16):
        tk = t0 + k * BEAT / 4
        if tk >= END:
            break
        m = arp[k % 8]
        nn = int(0.22 * SR)
        tt = np.arange(nn) / SR
        s = (np.sin(2 * np.pi * note(m) * tt) + 0.3 * np.sin(4 * np.pi * note(m) * tt)) * env(nn, 0.002, 0.07)
        place(pluck, s * (1.0 if k % 4 == 0 else 0.7), tk)
    # drums
    for k in range(4):
        tk = t0 + k * BEAT
        if tk >= END:
            break
        nn = int(0.35 * SR); tt = np.arange(nn) / SR
        f = 45 + 110 * np.exp(-tt * 28)
        kick = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-tt * 9)
        place(drums, kick * 1.0, tk)
        place(kick_env, np.exp(-tt * 7), tk)
        if k in (1, 3):
            nn2 = int(0.2 * SR)
            clap = rng.standard_normal(nn2) * env(nn2, 0.001, 0.05)
            clap = clap - lowpass(clap, 900)
            place(drums, clap * 0.55, tk)
        for h in (0.5,):
            nn3 = int(0.06 * SR)
            hat = rng.standard_normal(nn3) * env(nn3, 0.0005, 0.015)
            hat = hat - lowpass(hat, 6000)
            place(drums, hat * 0.35, tk + h * BEAT)

# riser into the drop
rn = int(DROP * SR)
riser = rng.standard_normal(rn) * np.linspace(0, 1, rn) ** 2
riser = riser - lowpass(riser, 1500)
place(sfx, riser * 0.25, 0)

# whoosh on every cut + soft "tap" just after
for c in CUTS:
    wn = int(0.35 * SR)
    w = rng.standard_normal(wn)
    w = lowpass(w, 2500) * np.sin(np.linspace(0, np.pi, wn)) ** 2
    place(sfx, w * 0.9, max(0, c - 0.25))

# notification "ding"
if DING is not None:
    dn = int(0.9 * SR); tt = np.arange(dn) / SR
    ding = (np.sin(2 * np.pi * note(88) * tt) + 0.6 * np.sin(2 * np.pi * note(95) * tt)) * env(dn, 0.002, 0.25)
    place(sfx, ding * 0.35, DING)
    place(sfx, (np.sin(2 * np.pi * note(93) * tt) * env(dn, 0.002, 0.3)) * 0.3, DING + 0.12)

pad = lowpass(pad, 1400)
duck = 1 - 0.55 * np.clip(kick_env, 0, 1)   # sidechain pump
mix = 0.32 * pad * duck + 0.5 * bass * duck + 0.22 * pluck + 0.55 * drums + 0.5 * sfx
# ending: fade the last 1.2 s
fade = np.ones(N); fl = int(1.2 * SR); fade[-fl:] = np.linspace(1, 0, fl) ** 1.5
fade[: int(0.05 * SR)] = np.linspace(0, 1, int(0.05 * SR))
mix *= fade
mix = np.tanh(mix * 1.3) / np.tanh(1.3)
mix *= 0.89 / np.max(np.abs(mix))
stereo = np.stack([mix, mix], axis=1)
# a touch of width: delay the pluck slightly on the right channel
pl = 0.22 * pluck * fade
d = int(0.012 * SR)
stereo[d:, 1] += pl[:-d] * 0.15
stereo[:, 0] += pl * 0.0
stereo /= max(1.0, np.max(np.abs(stereo)) / 0.95)
with wave.open(out, 'wb') as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
    w.writeframes((stereo * 32767).astype('<i2').tobytes())
print('ok', DUR)
