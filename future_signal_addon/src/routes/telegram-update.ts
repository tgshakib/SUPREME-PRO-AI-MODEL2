import { Router, type IRouter } from "express";
import { handleTelegramUpdate } from "../bot";
import { logger } from "../lib/logger";

const router: IRouter = Router();

router.post("/internal/telegram-update", async (req, res) => {
  const expected = process.env["SESSION_SECRET"];
  const supplied = req.header("x-future-relay-secret");
  if (!expected || !supplied || supplied !== expected) {
    res.status(401).json({ ok: false, error: "unauthorized" });
    return;
  }
  try {
    await handleTelegramUpdate(req.body);
    res.json({ ok: true });
  } catch (err) {
    logger.error({ err }, "Relayed Telegram update failed");
    res.status(500).json({ ok: false, error: "update_failed" });
  }
});

export default router;