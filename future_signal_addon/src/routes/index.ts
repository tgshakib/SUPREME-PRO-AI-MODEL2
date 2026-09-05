import { Router, type IRouter } from "express";
import healthRouter from "./health";
import telegramUpdateRouter from "./telegram-update";

const router: IRouter = Router();

router.use(healthRouter);
router.use(telegramUpdateRouter);

export default router;
