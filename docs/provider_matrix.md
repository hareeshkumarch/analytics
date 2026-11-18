# Provider & Routing Matrix

| Provider | Adapter | API Profile | Continuation / Cost Notes | Contract Tested | Live Verified |
| --- | --- | --- | --- | --- | --- |
| OpenAI | `OpenAiResponsesAdapter` | `openai_responses` (`/responses`) | `text.format` strict JSON; `call_id`; `store=False` | Yes (MockTransport) | Pending (needs `OPENAI_API_KEY`) |
| Anthropic | `AnthropicMessagesAdapter` | `anthropic_messages` (`/v1/messages`) | `output_config.format`; `tools[].strict`; distinct `refusal` | Yes (MockTransport) | Pending (needs `ANTHROPIC_API_KEY`) |
| Gemini | `GeminiInteractionsAdapter` | `gemini_interactions` (`/v1beta/interactions`) | `store=False`; `total_thought_tokens` | Yes (MockTransport) | Pending (needs `GEMINI_API_KEY`) |
| Groq | `GroqChatAdapter` | `groq_chat` (`/chat/completions`) | Enforces `structured_with_tools=False` | Yes (MockTransport) | Pending (needs `GROQ_API_KEY`) |
| xAI | `XaiResponsesAdapter` | `xai_responses` (`/responses`) | `store=False`; exact `cost_in_usd_ticks` Decimal parsing | Yes (MockTransport) | Pending (needs `XAI_API_KEY`) |
| Kimi | `KimiChatAdapter` | `kimi_chat` (`/chat/completions`) | Preserves `reasoning_content` same-provider only | Yes (MockTransport) | Pending (needs `MOONSHOT_API_KEY`) |
| NVIDIA NIM | `NvidiaNimAdapter` | `nvidia_nim_chat` (`/chat/completions`) | Hosted catalog profile; unknown pricing excluded under hard ceiling | Yes (MockTransport) | Pending (needs `NVIDIA_NIM_API_KEY`) |
