# rewire — 2:00 demo video plan

Deadline: Sunday Nov 15, 8:00 am EST. Have the upload started by 7:30. Uploads are slow.

**A full 2:00 cut already exists: `rewire_cut_v1.mp4`.** It plays start to finish with a stand-in voice. Your job is to put your faces and your voices into it.

Everything is in `HighWire-work/video/` on Sohum's laptop. The clips are not in git because they show licensed Vexcel imagery.

## The idea in one line

Fires started by power lines keep killing people. Rewire shows a grid operator the danger on a 3D map of the real grid, has agents work out a fix, checks it with physics, and makes it one click.

## What wins (from past winners and judge guides)

- Show the product in the first 30 seconds. Judges often watch only the video.
- About 30% problem, 70% solution. No code, no terminals.
- One real story beats a feature list. Ours: the morning of the Woolsey fire.
- Polish counts: a title, music, clean cuts. Record everything twice.

## The story hour (real data, already in the app)

Woolsey replay, Thursday Nov 8, 2018. The app uses only what was known at each hour.

| Hour | What the app shows |
|---|---|
| 12 AM | Watch is green. All 864 lines fine. |
| 6 AM | Watch turns yellow. |
| 7 AM | Watch turns red. Agents are called. |
| 10 AM | One line at 100% of its fire alarm. Plan: turn off 1 line, 43,600 customers lose power, all hospitals stay on. |
| 2:22 PM | The real Woolsey fire starts. After you approve, the app marks the spot on the map. |

Open it yourself: `http://localhost:5173/?event=E1&hour=10`

## The cut, second by second

| Time | Length | Picture (file) | Part | Voice |
|---|---|---|---|---|
| 0:00 | 12 s | `cards/M0_cold_open.mp4` | 0 · Cold open | Speaker 1 |
| 0:12 | 15 s | `cards/MP_problem.mp4` → **FILM-1 goes over this** | 1 · The problem | Speaker 1, on camera |
| 0:27 | 3 s | `cards/M1_logo.mp4` | logo sting | none |
| 0:30 | 7 s | `clips/A1_wide_orbit.mp4` | 2 · See it | Speaker 2 |
| 0:37 | 9 s | `clips/A2_clock_to_red.mp4` (sped up 1.9×) | | |
| 0:46 | 2.5 s | `clips/A2_watch_closeup.mp4` | | |
| 0:48.5 | 9.5 s | `clips/A3_dive_hazard.mp4` | | |
| 0:58 | 2 s | `stills/B0_full_app_10am.png`, slow push in | 3 · Decide | Speaker 3 |
| 1:00 | 13 s | `clips/B1_agents.mp4` | | |
| 1:13 | 9 s | `clips/B2_plan.mp4` | | |
| 1:22 | 5.5 s | `clips/C1_approve.mp4` | 4 · Act | Speaker 4 |
| 1:27.5 | 3 s | `cards/M5_phone.mp4` → **hardware take goes over 1:25.5–1:30.5** | | |
| 1:30.5 | 4 s | `clips/C2_holds.mp4` | | |
| 1:34.5 | 9.5 s | `clips/C3_dark_lines.mp4` | | |
| 1:44 | 7 s | `cards/M4_landscape.mp4` | 5 · Proof | Speaker 1 |
| 1:51 | 5 s | `cards/M2_proof.mp4` | | |
| 1:56 | 4 s | `cards/M6_end.mp4` → **FILM-4 goes over this** | 6 · Close | all four |

## Script (about 270 words)

**0 · Cold open · 0:00–0:12 · Speaker 1, voice only, slow and flat**

> November 2018. The Woolsey fire destroys sixteen hundred buildings.
> January 2025. The Eaton fire kills nineteen people.
> Investigators traced both to power-line equipment.

**1 · The problem · 0:12–0:27 · Speaker 1, on camera (FILM-1)**

> When the winds hit, a grid operator has minutes to decide: leave a line on and risk a spark, or cut power to thousands of homes.
> Today they make that call across separate screens.
> We built one.

0:27–0:30: logo sting, no words.

**2 · See it · 0:30–0:58 · Speaker 2**

> This is the real Los Angeles grid: eight hundred sixty-four lines, on real terrain.
> We replay the morning of the Woolsey fire, hour by hour, with only what was known at the time.
> A risk watch tracks five hard limits, and where each one is heading.
> At 7 AM, it turns red.
> By ten, the map shows why: wind damage across the hills, and one line at its fire alarm.

**3 · Decide · 0:58–1:22 · Speaker 3**

> Red calls in the agents.
> An orchestrator picks the specialists it needs: fire, wind, brownout, buildings. Each one writes its findings to files you can open.
> Then physics takes over. Every plan is run through a real power-flow simulation before you see it. No AI writes a number.
> The answer: switch off one line. Every hospital stays on.

**4 · Act · 1:22–1:44 · Speaker 4**

> One click to approve.
> The line goes dark, on the map and on our model grid.
> The operator gets a text.
> As the clock runs on, the fix holds.
> Four hours later, the real Woolsey fire started. In our replay, the line nearest the ignition ranked in the top one percent for fire risk.

**5 · Proof · 1:44–1:56 · Speaker 1**

> Fire maps show where the danger is. Grid software shows where the power flows.
> Rewire turns both into one checked plan.
> Engineers at [COMPANY 1] and [COMPANY 2] told us [SHORT QUOTE].

No quotes ready? Say this instead: "And every result is published, including where we lose."

**6 · Close · 1:56–2:00 · all four on camera (FILM-4)**

> Rewire. Keep the lights on. Keep the fire out.

## On camera: exactly what to say

Faces are on screen for about 35 of the 120 seconds. Everything else is your voice over the app.
Every on-camera line below is also in the script above, so the timing does not change.

### FILM-1 · The problem · 0:12–0:27 · 15 s

Pick one version. All three say the same words.

**A. One person (Speaker 1), looking into the lens**

> When the winds hit, a grid operator has minutes to decide: leave a line on and risk a spark, or cut power to thousands of homes.
> Today they make that call across separate screens.
> We built one.

**B. Two people, side by side, trading lines**

| Who | Says |
|---|---|
| 1 | When the winds hit, a grid operator has minutes to decide. |
| 2 | Leave a line on, and risk a spark. |
| 1 | Or cut power to thousands of homes. |
| 2 | Today they make that call across separate screens. |
| 1 and 2 | We built one. |

**C. All four, one line each, a hard cut between faces (about 4 s each)**

| Who | Says |
|---|---|
| 1 | When the winds hit, a grid operator has minutes to decide. |
| 2 | Leave a line on, and risk a spark. |
| 3 | Or cut power to thousands of homes. |
| 4 | Today that call is made across separate screens. We built one. |

How: chest-up, eye level, phone in landscape on a stack of books. The laptop with the Rewire map glows behind you, out of focus. Say "We built one" and hold still for one second; the logo cuts in there.

### Part openers · 2 to 3 s each · each speaker starts their part on camera

You say your first sentence to the camera, then the picture cuts to the app while you keep talking.

| Slot | Time | Who | Says on camera | What you do |
|---|---|---|---|---|
| FILM-2a | 0:30–0:33 | Speaker 2 | This is the real Los Angeles grid. | Stand beside the screen, turn your hand toward the map as you say "grid". |
| FILM-3a | 0:58–1:00 | Speaker 3 | Red calls in the agents. | Sit at the laptop. Point at the red light on the screen, then look up at the camera. |
| FILM-4a | 1:22–1:24 | Speaker 4 | One click to approve. | Finger over the trackpad. The click on screen lands right after your words. |

Short on time? Skip these three and use the voice only. Nothing else changes.

### The hardware take · 1:25.5–1:30.5 · 5 s · one take, up to three people

One slow move of the phone camera, left to right: laptop, then the LED strip, then the phone.

| Time | Who is in frame | Says | What happens |
|---|---|---|---|
| 1:25.5–1:28 | Person at the laptop, person holding the strip | The line goes dark, on the map and on our model grid. | The click has just happened. The red patch on the strip goes dark while the camera is on it. |
| 1:28–1:30.5 | Person holding the phone | The operator gets a text. | The phone lights up. Hold it toward the camera. |

One-person version: Speaker 4 says both lines off camera and the shot is hands only.

Before the take: run `python -m backend.lights event` so the patch is red, and turn the bell on in the app so the text sends. The Twilio trial sends its stock wording, so keep the phone far enough away that the words are not readable. If the text does not arrive, leave the phone card in.

### FILM-5 (optional) · Who wants it · 1:51–1:56 · 5 s · Speaker 1

> Engineers at [COMPANY 1] and [COMPANY 2] told us [SHORT QUOTE].

Say it to the camera, plainly. Or keep it as voice and show the quotes card (see "Adding the two quotes").

### FILM-4 · The close · 1:56–2:00 · 4 s · all four

Pick one.

| Version | Who | Says |
|---|---|---|
| A. Together | All four | Rewire. Keep the lights on. Keep the fire out. |
| B. Split | 1, then 2, then 3, then all | Rewire. / Keep the lights on. / Keep the fire out. / (all four nod or smile) |
| C. One voice | Speaker 1, others beside them | Rewire. Keep the lights on. Keep the fire out. |

Stand shoulder to shoulder, same framing as FILM-1. Hold still for two seconds after the last word. Let the end card show for the last second.

### Each person's lines on one page

**Speaker 1 (the problem, the proof, the close)**

| When | Camera or voice | Line |
|---|---|---|
| 0:00–0:12 | Voice | November 2018. The Woolsey fire destroys sixteen hundred buildings. January 2025. The Eaton fire kills nineteen people. Investigators traced both to power-line equipment. |
| 0:12–0:27 | **Camera** | FILM-1 (version A, B or C) |
| 1:44–1:51 | Voice | Fire maps show where the danger is. Grid software shows where the power flows. Rewire turns both into one checked plan. |
| 1:51–1:56 | Voice or **camera** | Engineers at [COMPANY 1] and [COMPANY 2] told us [SHORT QUOTE]. |
| 1:56–2:00 | **Camera** | The close |

**Speaker 2 (the 3D map)**

| When | Camera or voice | Line |
|---|---|---|
| 0:30–0:33 | **Camera** | This is the real Los Angeles grid. |
| 0:33–0:58 | Voice | Eight hundred sixty-four lines, on real terrain. We replay the morning of the Woolsey fire, hour by hour, with only what was known at the time. A risk watch tracks five hard limits, and where each one is heading. At 7 AM, it turns red. By ten, the map shows why: wind damage across the hills, and one line at its fire alarm. |
| 1:56–2:00 | **Camera** | The close |

**Speaker 3 (the agents)**

| When | Camera or voice | Line |
|---|---|---|
| 0:58–1:00 | **Camera** | Red calls in the agents. |
| 1:00–1:22 | Voice | An orchestrator picks the specialists it needs: fire, wind, brownout, buildings. Each one writes its findings to files you can open. Then physics takes over. Every plan is run through a real power-flow simulation before you see it. No AI writes a number. The answer: switch off one line. Every hospital stays on. |
| 1:56–2:00 | **Camera** | The close |

**Speaker 4 (the action and the hardware)**

| When | Camera or voice | Line |
|---|---|---|
| 1:22–1:24 | **Camera** | One click to approve. |
| 1:25.5–1:30.5 | **Camera** (hardware take) | The line goes dark, on the map and on our model grid. The operator gets a text. |
| 1:30.5–1:44 | Voice | As the clock runs on, the fix holds. Four hours later, the real Woolsey fire started. In our replay, the line nearest the ignition ranked in the top one percent for fire risk. |
| 1:56–2:00 | **Camera** | The close |

### Filming rules

- Phone in landscape, 1080p, 30 fps, locked on books or a tripod. Never handheld except the hardware take.
- Light on the face from the front. A laptop showing a white page works.
- Two takes of everything. Clap once before each take so the sound is easy to line up.
- Leave one second of silence before and after you speak.
- Voice lines: phone voice memo, quiet room, a hand's width from your mouth. Play the cut on mute while you read so your timing fits the picture.
- Names: no spoken introductions. Put each name and role as small text under the face the first time it appears.
- Face in a corner (optional): the bottom-right corner is kept clear in every 3D clip, so a small circle of the speaker fits there.

## What is in the folder

| File | Length | What it is |
|---|---|---|
| `rewire_cut_v1.mp4` | 2:00 | The whole video with the stand-in voice. Start here. |
| `rewire_cut_v1_no_voice.mp4` | 2:00 | Same picture, no sound. Use this one in the editor. |
| `clips/A1_wide_orbit.mp4` | 8.7 s | Whole LA grid in 3D, calm and green, slow turn. No panels. |
| `clips/A2_clock_to_red.mp4` | 16.7 s | Full app: the clock runs midnight to 7 AM, the watch goes green, yellow, red, and the alert pops. |
| `clips/A2_watch_closeup.mp4` | 16.7 s | The same moment, close on the watch light and the agent box. |
| `clips/A3_dive_hazard.mp4` | 10 s | 10 AM, no panels: fly to the fire hazard through its label, tilt, turn around it. |
| `clips/B1_agents.mp4` | 13 s | The agent graph running, with the five steps lighting up beside it. |
| `clips/B2_plan.mp4` | 9.2 s | Close on the plan and its numbers. |
| `clips/C1_approve.mp4` | 8.4 s | Full app: the pointer clicks Approve, the lines go dark, the real fire's spot appears. |
| `clips/C1_approve_closeup.mp4` | 8.4 s | The same click, close on the plan box. |
| `clips/C2_holds.mp4` | 7 s | The clock moves on and what was switched off stays off. |
| `clips/C3_dark_lines.mp4` | 8 s | No panels: the dark lines and the label "Woolsey fire starts here at 2:22 PM". |
| `cards/M0_cold_open.mp4` | 12 s | The two fires, the numbers, the power line and the spark. |
| `cards/MP_problem.mp4` | 16 s | The operator's choice in words. Stands in for FILM-1. |
| `cards/M1_logo.mp4` | 3 s | The rewire mark draws on. |
| `cards/M2_proof.mp4` | 5 s | Top 1%, 95%, 8 tests. |
| `cards/M3_quotes.mp4`, `M3_quotes_blank.mp4` | 5 s | Two quote boxes. The blank one has no words so you can type over it. |
| `cards/M4_landscape.mp4` | 5 s | Fire maps, grid software, Rewire. |
| `cards/M5_phone.mp4` | 3 s | Phone alert. Stands in for the phone shot. |
| `cards/M6_end.mp4` | 4 s | Logo, the line, the repo link. |
| `cards/cap_*.png` | | The caption boxes, see-through, if you rebuild a section. |
| `stills/*.png` | | Sharp stills of every key moment, for the Devpost gallery and thumbnail. |
| `audio/scratch_voice_part0..6.m4a` | | The stand-in voice, one file per part. |
| `tools/` | | The scripts that recorded and joined all of this. |

## Edit steps (CapCut, iMovie or DaVinci)

1. Put `rewire_cut_v1_no_voice.mp4` on the timeline.
2. Lay each FILM clip on top at its time from "The cut, second by second".
3. Record each speaker's voice and place it at the start of their part.
4. Add your two quotes (below), or keep the proof card as it is.
5. Add one quiet music track under everything at about 15% volume. End it at 1:56.
6. Export 1080p, 30 fps, MP4. Watch it once on a phone with sound.
7. Upload. Paste the link in the submission.

If time runs out: `rewire_cut_v1.mp4` is a complete video. Record your four voices over it and ship.

### Adding the two quotes

Trim `M4_landscape` to 5 s and `M2_proof` to 3 s. Put `M3_quotes_blank.mp4` (4 s) between M2 and the end card and type the two quotes into the boxes. Use the emails' exact words, and ask before showing a name or a company.

## Effects already in the cut, and ones to add

- In: fade from black at 0:00, caption boxes on the 3D clips, a red flash when the watch turns red at 0:46, the slow push into the app at 0:58, the fade out at 2:00.
- Add: a low hit under the logo at 0:27, an alarm tone at 0:46, half a second of silence on the Approve click at 1:24.

## Things to say and not say

- Say "top one percent" for Woolsey. The exact number is the top 1.3% of 829 overhead lines.
- If asked: the wire that sparked Woolsey was a small 16 kV circuit that our grid model does not include. The claim is about the nearest line we model.
- Say "95% of flagged line-hours were inside a Red Flag Warning" and "every result is published, including where we lose".
- Do not read out the 94% in the top bar. It is a placeholder estimate, not a measured result.
- Do not say Rewire would have stopped the Woolsey fire. Say what the replay showed.
- The plan at 10 AM switches off a line about 9 km from where the fire started. Do not say it was the line that started it.

## Landscape (for the card and for questions)

| Who | What they do | What is missing |
|---|---|---|
| Technosylva, Pano AI, Overstory | Fire spread, cameras, vegetation risk | They show where the danger is, not what to switch |
| GE, Siemens, Hitachi grid software | Run the grid, power flow | No fire or storm picture |
| Rewire | Risk on the real grid, a plan from agents, checked by power flow, who loses power by building | |

## How it was made

- The 3D and app clips are real recordings of Rewire, taken frame by frame at 30 fps from a second copy of the app, so the live demo and the LED strip were not touched. The camera moves use the app's own controls: a hazard label to fly in, the tilt buttons, right-drag to turn.
- The three background pictures (fire on the ridge, the dark town, the control room) were made on Higgsfield. They are marked as illustrations on screen. That used 6 of the 10 credits; 4 are left. A 5-second Higgsfield video costs 35 credits and the full voice about 10, so neither was possible.
- The title cards are drawn in the Rewire font and colours.
- The stand-in voice is the Mac's built-in voice. Replace it with yours.

## Sources

- Woolsey: 96,949 acres, 1,643 structures, 3 deaths; CPUC safety division report names Southern California Edison's Big Rock 16 kV circuit.
- Eaton: 19 deaths, more than 9,400 structures; Los Angeles County Fire and Cal Fire found it started at an idle Edison transmission tower (reported August 2026). Edison disputes full responsibility.
- Rewire's own numbers: `validation/REPORT.md` (V5, V7).
