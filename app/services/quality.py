"""Tech-side quality presets (playbook Section 7) and which path applies
each value. See docs/vdo-notes.md.

- scale and bitrate: viewer side (iframe API {scale}, {bitrate})
- framerate: publisher side, sent to the customer page, which posts the
  Companion command {action: "videoConstraint", value: "frameRate"} to its
  own frame (applyConstraints on the live track, no reload). Publisher
  framerate applies to every viewer of that stream.
"""
PRESETS = {
    "clear":    {"label": "Clear",    "max_height": None, "scale": 100, "bitrate": 6000, "fps": 5},
    "balanced": {"label": "Balanced", "max_height": 1080, "scale": 100, "bitrate": 2500, "fps": 15},
    "smooth":   {"label": "Smooth",   "max_height": None, "scale": 50,  "bitrate": 1500, "fps": 30},
}
DEFAULT = "balanced"
THUMB = {"bitrate": 150, "width": 320}
