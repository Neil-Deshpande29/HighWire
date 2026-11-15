# rewire — 2:00 demo video plan

Deadline: Sunday Nov 15, 8:00 am EST. Have the upload started by 7:30. Uploads are slow.

**The finished cut is `rewire_cut_v3.mp4` (1:56, 1080p).** It is built from the takes in `film/`. `rewire_cut_v3_no_music.mp4` is the same cut without the music bed. `rewire_cut_v2.mp4` is the cut before the extra voice recording, and `rewire_cut_v1.mp4` is the older stand-in version; the sections below the notes on the finished cut describe how that one was planned.

Everything is in `HighWire-work/video/` on Sohum's laptop. The clips are not in git because they show licensed Vexcel imagery.

## The finished cut: what is where

Your voices play at their real speed. Only the cards and app clips were retimed to fit the words.

| Time | Picture | Voice (take) |
|---|---|---|
| 0:00 | Cold open card: the two fires | `01_coldopen_voice`, face in a circle, bottom right |
| 0:12 | The four of you, full screen. The control-room picture covers "made across multiple screens" (0:21 to 0:23.6) | `02_intros` |
| 0:25.6 | Logo | music only |
| 0:28.2 | 3D map, the clock runs, the watch turns red on the word "red" (0:44.5), dive to the hazard | `04_part2_webcam`, circle |
| 0:51.6 | Agent graph, shared files, physics check, the plan | `03_part2_webcam`, circle |
| 1:17.3 | The Approve click lands on the word "approve" (1:19.3), the line goes dark | `05_part4_webcam`, first 4.6 s, circle |
| 1:23.3 | Phone alert card, then the dark lines and the real fire's label, with two captions. Slowed so the captions can be read | `10_proof_voice`, the extra recording |
| 1:36.4 | Landscape card, then the proof card, both moved left | `0708_proofquote`, large circle on the right |
| 1:51.6 | End card, with rewire.wiki | `09_close` |

### What was changed, and why

- **File names.** `03_part2_webcam` is the agents part and `04_part2_webcam` is the map part. They are placed by what is said.
- **Speaker 4's last sentence is not in the cut.** The take says the line we flagged "ended up igniting in the Woolsey fire". It did not: the line the plan switches off is about 9 km from where the fire started. The take stops after "The line then powers off."
- **Four words are cut from the extra recording.** It says "the line that caused this fire was ranked top one percent". The wire that started the fire is a small 16 kV circuit our grid model does not include, so "that caused this fire" is cut. It now says "in our replay, the line was ranked top one percent for fire risk", and the caption says which line: the one nearest the real ignition, out of 829.
- **No hardware shot**, so the phone card stays.
- **The two company names are spoken, not written on screen.** Their spelling was not checked against the emails.
- **Circles.** Every take except `02_intros` is cropped to a circle around the face. The crop boxes are in `tools/assemble2.py` if a face needs re-centring.

### How it was built

- `tools/assemble2.py` joins everything: `python3 -I assemble2.py`. Add `only-audio` to redo just the sound, `no-music` to leave the bed out, `redo=S5_act,S7_close` to re-render only those sections. `tools/newvo.sh` splits the extra recording into its three sentences. It expects the takes converted to standard colour first (`avconvert --preset Preset1920x1080`, built into macOS), because the phone files are HDR 4K.
- Higgsfield: the three pictures (fire ridge, dark town, control room) and the music bed (27 s, looped quietly under the whole cut, louder where nobody speaks). Higgsfield does not allow unlimited generations from Claude Code, so only credits counted; they are now used up.
- Word timings came from a speech-to-text model run on this laptop. Nothing was uploaded.

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
| 0:12 | 15 s | `cards/MP_problem.mp4` → **the four faces go over this** | 1 · The problem | all four, on camera |
| 0:27 | 3 s | `cards/M1_logo.mp4` | logo sting | none |
| 0:30 | 7 s | `clips/A1_wide_orbit.mp4` | 2 · See it | Speaker 2 |
| 0:37 | 9 s | `clips/A2_clock_to_red.mp4` (sped up 1.9×) | | |
| 0:46 | 2.5 s | `clips/A2_watch_closeup.mp4` | | |
| 0:48.5 | 9.5 s | `clips/A3_dive_hazard.mp4` | | |
| 0:58 | 2 s | `stills/B0_full_app_10am.png`, slow push in | 3 · Decide | Speaker 3 |
| 1:00 | 13 s | `clips/B1_agents.mp4` | | |
| 1:13 | 9 s | `clips/B2_plan.mp4` | | |
| 1:22 | 5.5 s | `clips/C1_approve.mp4` | 4 · Act | Speaker 4 |
| 1:27.5 | 3 s | `cards/M5_phone.mp4` → **the hardware shot goes over 1:25.5–1:30.5** | | |
| 1:30.5 | 4 s | `clips/C2_holds.mp4` | | |
| 1:34.5 | 9.5 s | `clips/C3_dark_lines.mp4` | | |
| 1:44 | 7 s | `cards/M4_landscape.mp4` | 5 · Proof | Speaker 1 |
| 1:51 | 5 s | `cards/M2_proof.mp4` | | |
| 1:56 | 4 s | `cards/M6_end.mp4` → **the close goes over this** | 6 · Close | all four |

## Where your faces go

Every speaker is on screen at least three times: a full-screen line in the intro, a webcam circle through their own part, and the close. The software still fills the screen for 69 of the 120 seconds.

| Time | Picture | Faces |
|---|---|---|
| 0:00–0:12 | Cold open: the two fires | None. Speaker 1's voice. The first face lands harder after it. |
| 0:12–0:27 | **Four faces, full screen, one line each** | All four |
| 0:27–0:30 | Logo | None |
| 0:30–0:58 | 3D map | Speaker 2 in a circle, bottom right |
| 0:58–1:22 | Agents and the plan | Speaker 3 in a circle, bottom right |
| 1:22–1:44 | Approve, the dark lines, the real fire | Speaker 4 in a circle. Hands, LED strip and phone fill the screen at 1:25.5–1:30.5 |
| 1:44–1:56 | Proof cards, moved to the right half | **Speaker 1, large, on the left half** |
| 1:56–2:00 | Close | **All four, full screen** |

The bottom-right corner is clear in every software clip, so the circle never covers the map labels, the plan or the agent steps.

## Script

Three ways a line is delivered:

- **On camera**: your face fills the screen. Phone on a stack of books, look into the lens.
- **Corner**: you read to your laptop webcam. Your face sits in a circle over the software and your voice comes from the same take.
- **Off camera**: voice only.

### 0 · Cold open · 0:00–0:12 · off camera · Speaker 1

Slow and flat. About 11 seconds.

> November 2018. The Woolsey fire destroys sixteen hundred buildings.
> January 2025. The Eaton fire kills nineteen people.
> Investigators traced both to power-line equipment.

### 1 · The problem · 0:12–0:27 · on camera · all four, one line each

A hard cut between faces. Same framing for all four.

| Who | Says | Length |
|---|---|---|
| Speaker 1 | When the winds hit, a grid operator has minutes to decide. | 3.5 s |
| Speaker 2 | Leave a line on, and risk a spark. | 3 s |
| Speaker 3 | Or cut the power, and thousands of homes go dark. | 3.5 s |
| Speaker 4 | Today that call is made across separate screens. We built one. | 5 s |

Speaker 4: after "We built one", hold still for one second. The logo cuts in there.

### 2 · See it · 0:30–0:58 · corner · Speaker 2

One take, about 26 seconds.

> This is the real Los Angeles grid: eight hundred sixty-four lines, on real terrain.
> We replay the morning of the Woolsey fire, hour by hour, with only what was known at the time.
> A risk watch tracks five hard limits, and where each one is heading.
> At 7 AM, it turns red.
> By ten, the map shows why: wind damage across the hills, and one line at its fire alarm.

### 3 · Decide · 0:58–1:22 · corner · Speaker 3

One take, about 22 seconds.

> Red calls in the agents.
> An orchestrator picks the specialists it needs: fire, wind, brownout, buildings. Each one writes its findings to files you can open.
> Then physics takes over. Every plan is run through a real power-flow simulation before you see it. No AI writes a number.
> The answer: switch off one line. Every hospital stays on.

### 4 · Act · 1:22–1:44 · corner · Speaker 4

One take, about 20 seconds.

> One click to approve.
> The line goes dark, on the map and on our model grid.
> The operator gets a text.
> As the clock runs on, the fix holds.
> Four hours later, the real Woolsey fire started. In our replay, the line nearest the ignition ranked in the top one percent for fire risk.

While Speaker 4 says the second and third lines, the picture cuts to the hardware shot (no talking in that shot).

### 5 · Proof · 1:44–1:56 · on camera · Speaker 1

About 7 seconds, then about 5.

> Fire maps show where the danger is. Grid software shows where the power flows. Rewire turns both into one checked plan.

> Engineers at [COMPANY 1] and [COMPANY 2] told us [SHORT QUOTE].

The quote line can be said by whoever got the emails. No quotes ready? Say: "And every result is published, including where we lose."

### 6 · Close · 1:56–2:00 · on camera · all four

> Rewire. Keep the lights on. Keep the fire out.

Together, or split: Speaker 1 "Rewire." Speaker 2 "Keep the lights on." Speaker 3 "Keep the fire out." Speaker 4 nods. Hold still for two seconds after.

## What to record and send

Put the takes in `HighWire-work/video/film/` with these names. Any phone or webcam format works.

| File name | Who | How | Length |
|---|---|---|---|
| `01_coldopen_voice` | Speaker 1 | Voice memo | 11 s |
| `02_intro_1`, `02_intro_2`, `02_intro_3`, `02_intro_4` | each speaker | On camera | 3 to 5 s each |
| `03_part2_webcam` | Speaker 2 | Laptop webcam, one take | 26 s |
| `04_part3_webcam` | Speaker 3 | Laptop webcam, one take | 22 s |
| `05_part4_webcam` | Speaker 4 | Laptop webcam, one take | 20 s |
| `06_hardware` | hands only | Phone. No talking. Click Approve, the red patch on the strip goes dark, the phone lights up. | 8 s or more |
| `07_proof` | Speaker 1 | On camera | 7 s |
| `08_quote` | Speaker 1 or whoever got the emails | On camera | 5 s |
| `09_close` | all four | On camera | 4 s |
| `names.txt` | | Each speaker's name and role, for the small text under each face | |
| `quotes.txt` | | The two quotes, who said them, and whether the name and company can be shown | |
| music (optional) | | One quiet track | |

### How to film each kind

**On camera (02, 07, 08, 09)**

- Phone in landscape, 1080p, 30 fps, on a stack of books at eye level. Chest-up.
- Light the face from the front. A laptop showing a white page works.
- Same spot and framing for everyone, so the cuts between faces look clean.
- For `07_proof` and `08_quote`, stand a little left of centre. The card sits on the right half.

**Corner webcam (03, 04, 05)**

- Laptop webcam or phone, landscape. Face in the middle of the frame with space above the head; it gets cut into a circle.
- Look at the lens. Put the script right under it.
- Play `rewire_cut_v1_no_voice.mp4` beside you and read along so your words land on the picture.
- One take for the whole part. The voice comes from this take, so use a quiet room and sit close.

**Hardware (06)**

- Run `python -m backend.lights event` first so the patch is red. Turn the bell on in the app so the text sends.
- One slow move left to right: laptop, strip, phone. Room lights low.
- The Twilio trial sends its stock wording, so keep the phone far enough away that the words are not readable.

**All takes**

- Two takes of everything. Leave one second of silence before and after you speak.
- If a take runs a little long (up to about 8%) it can be tightened. Longer than that and the video runs past 2:00.

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
| `cards/MP_problem.mp4` | 16 s | The operator's choice in words. Stands in for the four faces. |
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

If you edit it yourselves instead of sending the takes:

1. Put `rewire_cut_v1_no_voice.mp4` on the timeline.
2. Lay each take on top at its time from "Where your faces go". Crop the webcam takes to a circle in the bottom-right corner.
3. Use the sound from the webcam takes as the voice for parts 2, 3 and 4.
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
