# Deployment Guide

## Environment Variables for Render

Add these in your Render dashboard for the **krabsleads-bot** worker:

| Variable | Required | Description |
|----------|----------|-------------|
| TELEGRAM_BOT_TOKEN | Yes | Telegram bot token |
| MONDAY_API_KEY | Yes | Monday.com API key |
| MONDAY_BOARD_ID | Yes | Monday.com board ID |
| ONETIMESECRET_USERNAME | Yes | OneTimeSecret username |
| ONETIMESECRET_API_KEY | Yes | OneTimeSecret API key |
| SUPABASE_URL | Yes | Supabase project URL |
| SUPABASE_KEY | Yes | Supabase anon key |
| DRIVER_TELEGRAM_ID | Optional | Legacy driver ID |
| GROUP_TELEGRAM_ID | Optional | Legacy group ID |
| SUPERVISORY_TELEGRAM_ID | Optional | Legacy supervisory ID |
| **OPENAI_API_KEY** | **Yes (for AI)** | OpenAI API key for AI vision extraction and missing-field detection (color, VIN, etc.) |

**OPENAI_API_KEY** is required for:
- AI vision (screenshot/image extraction)
- AI missing-field detection (prompts users when they omit color, VIN, car, etc.)

## Git Push and Vercel

1. **Push to Git:**
   ```bash
   git add .
   git commit -m "Add missing-field detection, file attachments, Eastern timezone"
   git push origin main
   ```

2. **Vercel (Admin Frontend):**
   - The admin dashboard lives in `admin-frontend/` (Next.js)
   - Vercel auto-deploys from your Git repo if connected
   - Ensure `admin-frontend` is the root or set root directory in Vercel project settings
   - Add env vars in Vercel: `NEXT_PUBLIC_API_URL` (e.g. your Render admin web URL)

3. **Render:**
   - Push triggers automatic redeploy if Render is connected to your repo
   - Add `OPENAI_API_KEY` in Render → krabsleads-bot → Environment
