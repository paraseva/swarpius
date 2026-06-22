# Known Limitations

Here we provide an accounting of what Swarpius can't do, what it struggles with, and where the boundaries are. Some of these are hard limits imposed by the Roon API, and some may be addressed in potential future features. This document is for contributors and users who want to understand the system's limits before expecting specific behaviour.

## Roon API Constraints

These limitations come from the Roon API itself, and place hard boundaries on what is currently possible in Swarpius.

### Queue manipulation is read-mostly

The Roon API exposes very limited queue operations. Swarpius can:
- View the queue
- Add items to the end (Queue) or insert next (Add Next)
- Jump to a specific track (Play From Here)
- Replace the queue entirely (Play Now)

Swarpius **cannot**:
- Remove individual tracks from the queue
- Reorder tracks within the queue
- Insert a track at a specific position
- Trim only the upcoming items while keeping the current track playing (Roon's "Clear queue" UI option). Using `stop` and re-adding the playing track is a workaround, but it will start playing from the beginning.

### Track unavailability is invisible

Some tracks in a Roon library may be temporarily or permanently unavailable (e.g. licensing changes, network issues with streaming services, corrupt local files). These are marked in the Roon app as "unavailable", but the Roon API does not report this status. When Swarpius plays or queues an unavailable track, the action appears to succeed but the track silently fails to play. There is no programmatic way to detect this at present. If you queue a certain number of tracks in Swarpius but some are missing, you can verify they are unavailable by looking them up in Roon.

### Stop relies on an installed silent marker track

Roon's API doesn't expose a real `stop` primitive — the native `stop` control aliases to pause. Swarpius implements a real `stop` (playback ended, queue emptied) by Play Now-ing a 500ms silent audio file in your Roon library. We provide the file at `assets/Swarpius Stop Simulation/Swarpius_Stop_Playback.wav`; full setup steps are in the [Stop marker](../README.md#stop-marker--required-for-the-stop-button) section in the root README.

When the marker isn't installed, the `stop` action silently falls back to `pause`. On each zone card the STOP button shows a muted style with a tooltip prompting setup; clicking it re-checks for the marker without affecting playback, so once the file is installed a single click transitions the button back to its normal state. Set `DISABLE_SIMULATED_STOP="true"` in `.env` to opt out entirely (button hidden, every stop action is just a pause).

A subtle case: if the marker file is **removed during agent uptime**, the next stop click reports success but the silent track doesn't actually play. The Roon API does not raise an error in this case (see [Track unavailability is invisible](#track-unavailability-is-invisible) above), so Swarpius can't tell the difference and won't auto-recover. Restart the agent to force re-detection.

### Album/playlist version selection

The API returns limited information about albums and tracks — the title, along with some extra information which generally contains the artists involved. There is no information about source (local, Tidal, Qobuz etc.), resolution (44.1kHz, 96kHz, 192kHz etc.) or format (FLAC, WAV, MP3 etc.). When an album exists in multiple versions with identical titles that can't be disambiguated, Swarpius just selects the first version Roon returns. Where titles denote versions with suffixes such as "(Extended Mix)", "(Remastered 2011)" or "(Deluxe Version)", Swarpius can find and target them specifically on request. Fortunately, many titles available on streaming services are labelled accordingly.

### No library metadata queries

Swarpius can search for specific content but cannot query library-level metadata. Examples of things it cannot answer:
- "What genres are in my library?"
- "What's my most played track?"
- "Show me all albums available in 192kHz"

The Roon API is browse-and-search oriented, not query oriented. There is no SQL-like interface to the library database.

### No playlist creation or modification

Swarpius can play existing playlists but cannot create new playlists, add tracks to playlists, or modify playlist contents. These operations are not exposed by the Roon API's browse interface.

### No favourites or ratings

There is no API endpoint for marking tracks as favourites, rating tracks, or modifying any user preference data.

### No crossfade, EQ, or DSP control

Audio processing settings (crossfade, parametric EQ, convolution, signal path configuration) are not accessible through the API.

### No sleep timer or scheduled playback

The Roon API does not support timers, alarms, or scheduled operations. "Play jazz at 7am" or "stop after 30 minutes" are not possible.

### No lyrics

Lyrics are not exposed through the browse API, even when they are available in the Roon app.

### Intermittent search availability

In cases where Roon search is intermittently unavailable (e.g. after a Roon Core restart, during streaming service re-authentication, or under heavy load), searches may return empty results for queries that would normally succeed. Swarpius mitigates this with automatic retry logic — searches that return no results are retried up to `ROON_SEARCH_RETRY_LIMIT` times (default 2) with a `ROON_SEARCH_RETRY_DELAY` (default 1 second) between attempts. This is transparent to the user; retried searches appear as normal results.

## LLM-driven Limitations

These come from the fact that Swarpius is powered by an LLM making decisions. LLMs can make mistakes, and these should be expected from time to time. Performance improves as you move up in model quality, with diminishing returns at the top end. Swarpius has many deterministic guardrails and error-handling mechanisms that feed appropriate messages back to the coordinator, helping it re-plan approaches and work around mistakes.

Note that the weakest models are unlikely to be effective for Swarpius beyond perhaps the simplest of requests. Models that perform well with agentic tasks, such as Claude Sonnet 4.6, Gemini 2.5 Pro, or GPT-5.4, are generally capable of complex multi-zone, multi-track search-and-queue operations, though the limitations below still apply.

### Ambiguous requests may be resolved incorrectly

When a search returns multiple results (e.g. "Thriller" matches an album, a track, tribute albums, and remixes), Swarpius picks one based on both context and its own baked-in musical knowledge. It usually gets this right, but not always. Specifying the type ("play the album Thriller") and artist ("by Michael Jackson") significantly improves accuracy.

### Vague intent may produce unexpected results

Requests like "play something chill" or "put on some background music" depend entirely on how the LLM interprets the intent and what search terms it generates. Results vary by model and are not reproducible. Enabling web search can significantly improve the results you obtain for e.g. genre-based requests.

### Context window limits

Swarpius maintains context within a conversation (recent searches, actions, and results), but this context is finite. Very long conversations or rapid sequences of complex operations may cause earlier context to be lost. The working set of context persists across a restart (so a restart resumes where you left off), but it is still bounded — older turns age out of the active context regardless of restarts.

### No learned preferences or habits

Chat history, working memory, and a listening-history record persist across restarts, and you can ask about what you played ("what did I listen to last Tuesday?"). But Swarpius does not *learn* preferences or infer habits: "play my usual morning music" is not possible, because nothing builds a model of your routines from past listening.

## Expansion and Shuffle Limitations

### Per-track queueing latency

When shuffling multiple playlists or albums together, Swarpius expands them into individual tracks and queues each one separately via the Roon browse API. This enables more complex requests such as "play 25 random tracks from playlist A, album B and tracks C, D and E". The process takes roughly 0.5-1 second per track added; a shuffle of 60 tracks across 3 playlists would take ~40-60 seconds. Playback starts immediately when the first track is added (the rest queue in the background), but the full queue build-up is not instant.

### No cross-library shuffle

"Shuffle my entire jazz collection" would require enumerating all tracks in a genre, which Swarpius's search-based library navigation doesn't currently support. Swarpius can shuffle specific playlists, albums, and tracks that have been explicitly searched for and referenced.

## TTS Limitations

### TTS hosting requires dedicated hardware

If you want to host an [F5-TTS](https://github.com/SWivid/F5-TTS) server, you will require an NVIDIA GPU with CUDA 12.8. There is no CPU fallback. Note that a good GPU will be needed for acceptable TTS performance - our tests were conducted with an NVIDIA RTX 5090, generating speech with around 1 second of latency.

### Long responses are truncated for speech

Responses longer than a few sentences are truncated to the first sentence for TTS. Full responses are always visible in the chat UI. List responses (search results, queue listings) are not spoken, but you may be able to specifically ask Swarpius to speak them.

## Web UI Limitations

### No offline support

The web UI requires an active WebSocket connection to the agent. There is no offline mode, cached state, or service worker.

### Browser speech input limitations

Speech input uses the browser's built-in Web Speech API. This has several constraints:

- **Browser support**: only Chromium-based browsers (Chrome, Edge, Brave) and Safari. Not supported in Firefox, where the microphone button is disabled.
- **Privacy**: Chrome and Edge send audio to Google's servers for processing. Safari uses on-device recognition.
- **Background music**: accuracy degrades significantly with music playing through speakers. A conference speaker or directional microphone helps by reducing what the mic picks up.
- **Not hands-free**: speech input populates the text field for review before sending. It does not auto-submit or support wake-word activation.

### Single concurrent user

The agent handles one request at a time. Connecting from another browser terminates the connection on any existing browser session. However, if you install the entire package on multiple devices on your network, they will act as independent instances, each establishing its own connection to your Roon Core, and each with its own configuration and logs.
