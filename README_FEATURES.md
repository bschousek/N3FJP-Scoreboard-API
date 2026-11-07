# N3FJP Field Day Scoreboard - Feature Guide

## 🎯 Overview
Real-time Winter/Summer Field Day scoreboard that connects to N3FJP logging software and displays live statistics, maps, charts, and bonus point calculations.

---

## 🆕 Latest Features

### 1. **Live Weather Display** 🌤️
Shows current temperature and weather icon in the header next to the clock.

**Setup:**
```json
{
  "weather_enabled": true,
  "weather_zip": "60050"
}
```

- Uses wttr.in (free, no API key needed)
- Auto-updates weather icons based on conditions
- Gracefully fails if no internet connection

---

### 2. **Band Goal Stars** ⭐
Visual indicators when band goals are achieved!

**Setup:**
```json
{
  "band_goals": {
    "160": 5,
    "80": 50,
    "40": 100,
    "20": 200,
    "15": 50,
    "10": 50,
    "6": 10,
    "2": 5
  }
}
```

**When goal is met:**
- Green glowing background
- Animated star in corner
- Tooltip shows goal

---

### 3. **Field Day Bonus Points Calculation** 🏆

Complete implementation of ARRL Field Day bonus scoring!

**Setup (config.json):**
```json
{
  "field_day_class": "2O",
  "emergency_power": true,
  "media_publicity": false,
  "public_location": true,
  "public_information_table": true,
  "nts_message_originated": 10,
  "nts_message_handled": 10,
  "satellite_qso": false,
  "w1aw_bulletin": false,
  "educational_activity": false,
  "social_media": true,
  "youth_participation": true,
  "site_visit_official": false
}
```

**Bonus Points:**
- 100% Emergency Power: 100 pts
- Media Publicity: 100 pts
- Public Location: 100 pts
- Public Information Table: 100 pts
- NTS Messages: 10 pts each (max 100)
- Satellite QSO: 100 pts
- W1AW Bulletin Copy: 100 pts
- Educational Activity: 100 pts
- Social Media: 100 pts
- Youth Participation: 100 pts
- Site Visit by Official: 100 pts

**Final Score Formula:**
```
(QSO Points × Class Multiplier × Power Multiplier) + Bonus Points
```

**Example:**
- Class: 2O (2 transmitters = 2x multiplier)
- Emergency Power: true (2x multiplier)
- QSO Points: 2175
- Bonus: 500 points
- **Final Score: (2175 × 2 × 2) + 500 = 9200 points**

---

### 4. **Live Rate Statistics with Fire Effect** 🔥

**Two new rate displays on Page 2:**
- Last 20 minutes (extrapolated to hourly)
- Last 60 minutes

**Fire effect automatically triggers at 100+ QSOs/hour:**
- Pulsing animation
- Orange glow
- Increased size

---

### 5. **Milestone Celebrations** 🎉

**Contact Milestones:**
- 100, 500, 1000, 1500, 2000, etc.

**Band Milestones:**
- First contact on each band

**Animation:**
- Full-screen overlay
- Huge glowing number/band
- Bouncing animation
- Auto-dismisses after 3 seconds

---

### 6. **Manual Navigation Controls**

**Header Buttons:**
- ⏸️ Pause / ▶️ Play
- ← Prev | Next →

**Keyboard Shortcuts:**
- **Arrow Keys**: Navigate pages
- **Spacebar**: Pause/Play
- **1, 2, 3, 4**: Jump to page

---

## 📊 Page Breakdown

### Page 1: Main Scoreboard
- Total contacts (big display)
- QSO Points + Bonus + **Final Score**
- Contacts/Points by operator (charts)
- Contacts by mode (pie chart)
- Top countries worked

### Page 2: Timeline & Performance
- Band activity grid (with goal stars ⭐)
- Timeline chart (hourly rate)
- Multipliers & best hour
- **Live rates with fire effect** 🔥

### Page 3: Active Stations
- Physical station locations
- Current operator per station
- Recent QSOs per station

### Page 4: World Map
- Offline outline map (Leaflet.js + bundled Natural Earth outlines, no API key)
- Worked US states, Canadian provinces, and DX countries shaded by contact count
- Hover for name and contact count
- Theme-aware (dark/light)

---

## 🛠️ Configuration Guide

### Minimal Setup
```json
{
  "n3fjp_host": "192.168.3.111",
  "n3fjp_port": 1100,
  "club_name": "Your Club Name",
  "callsign": "N0CALL",
  "event_name": "Field Day 2026"
}
```

### Full Setup with All Features
```json
{
  "club_name": "McHenry County Amateur Radio Club",
  "callsign": "N9WH",
  "event_name": "Winter Field Day 2026",
  "home_lat": 41.3,
  "home_lon": -88.4,
  "home_location": "McHenry, IL",
  
  "field_day_class": "2O",
  "emergency_power": true,
  "media_publicity": false,
  "public_location": true,
  "public_information_table": true,
  "nts_message_originated": 10,
  "nts_message_handled": 10,
  "satellite_qso": false,
  "w1aw_bulletin": false,
  "educational_activity": false,
  "social_media": true,
  "youth_participation": true,
  "site_visit_official": false,
  
  "weather_enabled": true,
  "weather_zip": "60050",
  
  "band_goals": {
    "160": 5,
    "80": 50,
    "40": 100,
    "20": 200,
    "15": 50,
    "10": 50,
    "6": 10,
    "2": 5
  }
}
```

---

## 🚀 Setup Instructions

### 1. Download Leaflet (one-time)
```bash
python download_leaflet.py
```

### 2. Edit Configuration
```bash
nano config.json
# Update with your club info and Field Day settings
```

### 3. Start Server
```bash
python -m uvicorn server:app --host 0.0.0.0 --port 8080
```

### 4. Open Browser
```
http://localhost:8080
```

### 5. Fullscreen Mode (for TV/projector)
```bash
chromium-browser --kiosk --app=http://localhost:8080
```

---

## 🎨 Customization Tips

### Adjust Band Goals
Edit `band_goals` in config.json based on your:
- Station class
- Expected propagation
- Operator experience
- Event duration

### Disable Weather
Set `weather_enabled: false` if no internet

### Change Home Location
Update `home_lat`, `home_lon`, `home_location` for accurate map

### Modify Page Rotation
Edit `setInterval(nextPage, 15000)` in index.html
- Default: 15 seconds per page

---

## 🐛 Troubleshooting

### Weather not showing
- Check internet connection
- Verify `weather_zip` is valid US zip code
- Check browser console for errors

### Band goals not appearing
- Ensure `band_goals` exists in config.json
- Restart server after config changes
- Check browser console

### Bonus points = 0
- Verify Field Day settings in config.json
- Check server logs for calculation errors
- Ensure `field_day_class` is set (e.g., "2O")

### Map not loading
- Run `python download_leaflet.py`
- Check `/www/lib/` directory exists
- Check `www/lib/geo/` contains the outline files (regenerate with `python build_map_data.py`)

---

## 📝 Credits

Created by: **KC9MNE**  
N3FJP Integration: Real-time API polling  
Maps: Leaflet.js + Natural Earth outlines  
Charts: Chart.js  
Weather: wttr.in  

**73!** 📡
