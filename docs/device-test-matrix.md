# Manual device test matrix

Run before go-live and after every VDO.Ninja or RustDesk upgrade. Use a real code from the dashboard and a second person on the tech side. Mark each cell PASS, FAIL (with a note) or N/A.

Record: screen share works, camera works, audio reaches the tech (after Unmute), cursor visible in the screen share, the "no audio" badge is right, RustDesk prompt offers the correct download (file name on Windows), ending from either side releases the capture (browser sharing indicator disappears).

| Device and browser | Screen | Camera | Audio | Cursor | RustDesk download | End releases capture | Notes |
|---|---|---|---|---|---|---|---|
| Windows 11, Chrome | | | | | | | Tick "Also share system audio" |
| Windows 11, Edge | | | | | | | |
| Windows 11, Firefox | | | | | | | No screen audio expected |
| macOS (current), Chrome | | | | | | | Screen Recording permission flow, browser restart, same session continues |
| macOS (current), Safari | | | | | | | Apple chip / Intel choice in RustDesk prompt |
| macOS (current), Firefox | | | | | | | |
| Ubuntu, Chrome | | | | | | | Wayland portal picker |
| Ubuntu, Firefox | | | | | | | |
| ChromeOS, Chrome | | | | | N/A | | RustDesk not offered |
| Android, Chrome | | | | | | | Screen hidden if not supported; apk permissions note |
| iPhone, Safari | N/A | | | N/A | N/A | | Share Screen hidden, camera only |
| iPad, Safari | N/A | | | N/A | N/A | | Desktop-mode iPad detected as iPad |

Network: one run from a restrictive network (UDP blocked, only TCP 3478 or 5349 allowed) to confirm relay fallback. On the session view the connection must still come up; System health relay test should show TCP or TLS relay candidates.

| Date | Tester | VDO.Ninja | RustDesk | Result |
|---|---|---|---|---|
| | | v30.2 | | |
