# לוזי — Instagram studio

Generates and schedules the weekly Instagram content for **@luzibyd** through Metricool.

## Weekly routine (run every Sunday morning for Mon–Sun of the coming week)

Cadence: **every day one post (10:00) + one reel (18:00)**; one of the week's posts (Wednesday) is a **carousel**.
Shabbat: Friday reel at 13:00 (before Shabbat), Saturday post 20:00 and reel 21:00 (after Shabbat).
Timezone `Asia/Jerusalem`. Metricool brand (blogId) **7516388**, Instagram only.

1. **Avoid repeats.** Read the previous plans in `marketing/plans/` (and `content-bank.md`). Pick topics and hooks
   that weren't used in the last 3 weeks; rotate audiences (tutor / cosmetician / barber / all) across the week.
2. **Write the plan** `marketing/plans/<monday-date>.json` (same shape as the existing ones):
   - `id` = `<date>-post` / `<date>-reel` / `<date>-carousel`, `date` = local time `YYYY-MM-DDTHH:MM:SS`.
   - `caption`: Hebrew, 3–8 short lines, ends with `🔗 הקישור בביו` and 4–7 hashtags incl. `#לוזי`.
   - Posts: `slides` (1 = single image, 4–8 = carousel). Reels: `scenes` with `dur` (seconds), 10–15 s total,
     first scene is a 2–2.6 s hook, last is `cta`. Vary `music` (`key` −5..6, `style` 0–3, `bpm` 110–126);
     set `ding` (seconds) when a scene shows a notification bubble.
3. **Render:** `node marketing/studio/render.mjs marketing/plans/<date>.json marketing/instagram/<date>`
   (needs Playwright/Chromium, ffmpeg, python3 + numpy; ~6 min). `ONLY=id1,id2` re-renders just those items.
   **Look at every image and a few frames of every reel** (ffmpeg `-ss T -frames:v 1`) before scheduling:
   no clipped/orphaned text, right screenshot for the message.
4. **Publish media:** commit `marketing/plans/<date>.json` + `marketing/instagram/<date>/`, push the branch, and use
   `https://raw.githubusercontent.com/dekelPK/tutor-app/<branch>/marketing/instagram/<date>/<file>` as media URLs
   (check they return 200). Metricool copies the files when the post is created.
5. **Schedule** each item with Metricool `createScheduledPost`: `providers:[{"network":"instagram"}]`,
   `instagramData.type` `POST` (images, carousel = several media in order) or `TRIAL_REEL` (one mp4 — the owner wants every reel published as a trial reel),
   `publicationDate:{dateTime, timezone:"Asia/Jerusalem"}`, `autoPublish:true`, `draft:false`.
   Then `getScheduledPosts` for the week to confirm all 14 are there, once each.
6. Open a PR with the plan + media and merge it after checks pass, so the history of plans stays on `main`.

## Slide templates (`type`)

All text fields accept `\n` for line breaks and `**bold**`. Every slide takes `theme`:
`blue` (brand), `light`, `white`, `warm` (pink — cosmeticians), `dark` (barbers), `mint`, `sun`.

| type | fields |
|---|---|
| `hook` | `emoji`, `title`, `sub`, `tag`, `foot`, `titleSize` |
| `phone` | `tag`, `title`, `sub`, `shot` (screenshot name) — posts: text beside phone; reels: phone below, scrolling |
| `list` | `tag`, `title`, `items: [[emoji, title, desc], …]` (≤3 for posts) |
| `bubble` | `title`, `sub`, `label`, `bubble` — a notification/message card |
| `stat` | `big`, `title`, `sub` |
| `vs` | `title`, `before[]`, `after[]`, `beforeTitle`, `afterTitle` |
| `offer` | `title`, `sub`, `foot` — LUZI29 coupon card |
| `cta` | `title` (default לוזי), `sub`, `foot` (default 🔗 הקישור בביו) — logo end card |

## Screenshots (`shots/`)

`<profession>-<screen>.jpg`, 1170×2532, from the demo accounts (owner "מיכל"):
professions `tutor`, `cosmetician`, `barber`; screens `dashboard`, `lessons` (upcoming), `unpaid`, `students`,
`student` (client profile sheet), `payments`, `calendar`, `newlesson` (add appointment form), `portal` (client's personal
link / self-booking), `trial` (trial-lesson form; not for barber).

## Facts to keep the copy honest

- Pricing: Basic ₪19, Pro ₪39; launch coupon **LUZI29** → ₪29/month. Professions: private tutors (incl. music
  teachers), barbers, cosmeticians.
- Clients get a **personal link**: see upcoming appointments, request a slot from your free times; you approve.
- The owner gets **push notifications** for new requests and a morning reminder. Reminders to clients are sent
  **by WhatsApp in one tap** (not fully automatic) — say "תזכורת בוואטסאפ בלחיצה".
- Payments: paid/unpaid per appointment, partial payments, punch cards (כרטיסיות), monthly summary.
- Services/price list with price + duration per service (cosmeticians, barbers).
- Tutors: trial lessons with a follow-up ("ממשיך?"), exams with 8:00 reminder, lesson summary + homework that the
  parent/student sees in the personal link.
- Never use the owner's personal email or name in content.
