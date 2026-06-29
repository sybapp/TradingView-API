# Allow gaps between RTH sessions

Versioned Datasets for RTH validation may contain overnight or weekend gaps between sessions, while still requiring bars to be continuous within each RTH session. This keeps ES RTH datasets realistic, avoids treating normal session breaks as corrupt data, and requires session-aware validation and flat-before-close logic instead of a single global five-minute adjacency rule.
