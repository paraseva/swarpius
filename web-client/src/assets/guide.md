# Swarpius Usage Guide

## Getting Started {#getting-started}

Swarpius is a conversational assistant for your Roon music system. You can ask it to play music, search your library, control your speakers, and answer questions.

### Initial Setup
1. **Add an LLM provider.** On the Settings page, pick a provider (such as Anthropic or OpenAI) and paste in your API key. **The model you use can significantly impact the experience.**
2. **Connect Roon.** Your Roon Core is detected automatically. On initial detection, open Roon and approve the "Swarpius" extension under Settings -> Extensions. If auto-discovery doesn't find it, try entering your Roon Core's address in the **Roon Core URL** field on the **Roon** tab in Settings.
3. **Start talking.** Try "play some Tchaikovsky on my default zone", "what's playing?", or "tell me about this artist".

### Optional extras 
Set up any time in Settings:
- **Web search** helps Swarpius tackle requests that need up-to-date information from the web.
- **Spoken replies** reads answers aloud when connected to an F5-TTS server.

### Finding your way around
This guide can be opened at any time from the **Getting Started** button on the Settings page. The **?** icons in different sections open short, context-specific help.

### Updating & managing the app
- **Your data is safe across updates.** Your settings, history, and Roon Core setup are stored outside the application folder. On the installed app that's `%LOCALAPPDATA%\Swarpius` (Windows), `~/Library/Application Support/Swarpius` (macOS), or `~/.local/share/swarpius` (Linux).
- **To update:** download the latest version and install it over the previous one. Your settings are preserved. On Windows, quit Swarpius first so the program isn't in use.
- **To remove it:** on Windows, uninstall via Settings -> Apps -> Installed Apps. On macOS or Linux, delete the application; also delete the data folder above if you want to erase your settings and history.

<!-- end-guidance -->

## Chat Basics {#chat-basics}

You can ask Swarpius to play music, control playback, answer questions, or a combination. Some examples:

- "Play Mozart in the kitchen"
- "What's playing?"
- "Skip 5 tracks ahead"
- "Queue up Abbey Road"
- "Play 10 random Pink Floyd tracks"
- "Tell me about the artist currently playing"
- "Transfer bedroom to kitchen"
- "Play the biggest UK hit of 1976 in the office"
- "What's the song playing in the bedroom about?"

Swarpius works through requests step by step — it searches your library, looks for the thing you want, and plays it. If something's ambiguous, it'll take a best guess, or ask you for clarification. If it helps, and you've set up web search, it'll search the internet for information that helps it carry out your request.

<!-- end-guidance -->

## Zones {#zones}

Zones represent your audio outputs in Roon. Swarpius discovers all available zones automatically when it connects to your Roon Core.

When performing playback actions, you can target any zone by the name you set in the Roon app, e.g. "play some Schubert on **&lt;zone name&gt;**". If you don't mention a zone, Swarpius uses your default zone, which is shown in a dropdown in the header. You can change your default zone via this dropdown, or by asking Swarpius directly, e.g. "change my default zone to **&lt;zone name&gt;**".

<!-- end-guidance -->

### Enabling the Stop button {#stop-marker}

<!-- audience: bundle -->

Roon has no true "stop" — its stop control only pauses. Swarpius ends playback and clears the queue by playing a half-second silent track, which you add to your Roon library once:

1. Use the button below to open the folder holding the silent track.
2. Copy the **Swarpius Stop Simulation** folder somewhere Roon will scan it. Your watched folders are listed in Roon under **Settings → Storage**, where you can also add a new one if needed.
3. Wait for Roon to scan it in, then click the STOP button on any zone card. It switches from its muted setup state to active once the track is found.

Until then, the STOP button stays in a muted setup state and stop requests fall back to pause. To disable the feature and remove the STOP button entirely, add `DISABLE_SIMULATED_STOP="true"` to your `.env` file and restart Swarpius.

<!-- end-guidance -->

## Text-to-Speech {#tts}

When text-to-speech is enabled, Swarpius speaks its responses aloud. Short replies are spoken in full; longer or list-heavy responses (like search results) are shortened to the opening sentence for speech. The full text is always in the chat.

Toggle text-to-speech on or off using the control in the header. TTS requires access to an F5-TTS server. If not configured, the toggle will be disabled.

<!-- end-guidance -->

## Live Diagnostics {#live-diagnostics}

<!-- audience: dev -->

Real-time view of what's happening in your current session, including LLM calls, token usage, and request traces. Toggle dev mode by double-clicking on the Swarpius logo (its background changes colour when on), and open the Live Diagnostics drawer with the grid icon in the header (or press Ctrl+Shift+D).

Available panels: Agents (coordinator steps), Tools (tool call details), Errors, Session Requests (request grouping and step-level breakdown), LLM Diagnostics (active call status, prompt composition, interrupt arbiter decisions), Prompt Budget (token allocation across prompt components), and Token Usage (per-call, per-minute, and session totals with cache breakdown).

<!-- end-guidance -->

## Conversation Analysis {#conversation-analysis}

<!-- audience: dev -->

The efficacy of your chosen LLM model for Swarpius can be evaluated via post-hoc quality reviews of past conversations. Your configured Analyser agent (best used with the most capable models) evaluates each conversation against a failure mode taxonomy, identifying issues like missed searches, hallucinated results, and unnecessary steps. Findings are linked to specific requests with severity ratings.

You can dispute findings and provide feedback; the system will extract lessons from your corrections to improve future analyses. As such, the general analysis guide provided with Swarpius is a set of universally applicable guidelines, while lessons are unique to your installation and constitute an environment-specific tuning.

<!-- end-guidance -->

## Costs {#costs}

Track what Swarpius spends on LLM calls.

The summary cards show total cost, request count, net tokens, and cache hit rate for the selected range. Below them are four views: **Cost over time** (a daily trend of tokens and spend), **Cost by agent** and **Cost by model** (where the spend goes), and **Mean cost per request, by complexity** (simple, compound, and complex requests, so you can see what heavier requests cost).

Use the **After** / **Before** date fields and the **Agent** / **Model** filters to narrow the view. Figures are calculated from each call's recorded token usage and model pricing, so treat them as a close guide rather than an exact invoice.

<!-- end-guidance -->

## Settings {#settings}

Configure Swarpius from the app instead of editing files by hand. Changes save to disk; click **Restart** to apply them.

**Models tab** picks the AI model for the assistant. Only the Coordinator is required to be set here. It handles all your chat requests, so picking a capable model is strongly recommended for the best experience. The three optional helpers (Arbiter, Diagnostic, Analyser) are off by default. Turn one on and either give it its own model or leave the fields blank to use the same model as the Coordinator. If two helpers use the same provider, they share the API key so you only need to enter it in one place.

**Test buttons** sit to the right of each agent row, the web-search service key fields, and the F5-TTS server URL. **OK** (green) means the saved value works. **Error** (red) means it doesn't, and hovering will display the reason. **Test** (neutral) means you've changed the field and not saved it yet; click to check the new value before saving.

**Save & Validate / Restart:** Save & Validate writes your changes to disk and re-runs the live checks. The button is only enabled when something is unsaved. Restart can be clicked at any time to restart Swarpius; for changes to take effect, you must Save & Validate first. The status line above the buttons tells you where you are: Unsaved changes / Validating / Saved & validated.

**Reload .env** at the top right re-reads from disk and drops any unsaved edits — handy as a "discard changes" button.

**Tab markers** light up when something needs attention. Red means required configuration is missing or a check has failed. Yellow means a configured service (web search or speech) isn't reachable. As long as the assistant is configured and working, Swarpius can be used even if other configurations are highlighted as faulty; those features just won't work until configured properly.

**The other tabs are optional.** **Roon** carries overrides for when auto-discovery doesn't find your Core. **Web Search** picks the search service (Brave, Tavily, or a self-hosted SearXNG). **Speech** points at an F5-TTS server for spoken replies. **Persona** gives the assistant a character or tone for its written replies. **Conversation Analyser** schedules the background analyser to run periodically.

<!-- end-guidance -->
