import express from "express";
import { WebSocketServer, WebSocket } from "ws";
import http from "http";
import fs from "fs";
import path from "path";

const HTTP_PORT = 6001;
const WS_PORT = 6000;

const app = express();
const uploadDir = path.join(process.cwd(), "recordings");
fs.mkdirSync(uploadDir, { recursive: true });

type LatestFrame = {
  userId: string;
  requestId: string;
  timestamp: number;
  mimeType: string;
  buffer: Buffer;
};

type PendingFrameMeta = {
  userId: string;
  requestId: string;
  timestamp: number;
  mimeType: string;
};

const latestFrames = new Map<string, LatestFrame>();
const viewers = new Set<WebSocket>();
const pendingMetaBySocket = new Map<WebSocket, PendingFrameMeta | null>();
const viewerSubscriptions = new Map<WebSocket, string | null>();

app.get("/", (_req, res) => {
  res.send("Relay server is running");
});

app.get("/latest-frame/:userId", (req, res) => {
  const frame = latestFrames.get(req.params.userId);

  if (!frame) {
    res.status(404).json({ error: "No latest frame found" });
    return;
  }

  res.json({
    userId: frame.userId,
    requestId: frame.requestId,
    timestamp: frame.timestamp,
    mimeType: frame.mimeType,
    imageBase64: frame.buffer.toString("base64"),
  });
});

app.get("/latest-frame-image/:userId", (req, res) => {
  const frame = latestFrames.get(req.params.userId);

  if (!frame) {
    res.status(404).send("No latest frame found");
    return;
  }

  res.setHeader("Content-Type", frame.mimeType);
  res.setHeader("Cache-Control", "no-store");
  res.send(frame.buffer);
});

const server = http.createServer(app);
server.listen(HTTP_PORT, () => {
  console.log(`HTTP server running on http://localhost:${HTTP_PORT}`);
});

const wss = new WebSocketServer({ port: WS_PORT });

function frameToDataUrl(frame: LatestFrame) {
  return `data:${frame.mimeType};base64,${frame.buffer.toString("base64")}`;
}

wss.on("connection", (ws, req) => {
  const url = req.url || "/";
  console.log(`Incoming connection: ${url}`);

  if (url.startsWith("/publish")) {
    console.log("Publisher connected");
    pendingMetaBySocket.set(ws, null);

    ws.send(
      JSON.stringify({
        type: "connected",
        role: "publisher",
      }),
    );

    ws.on("message", (data, isBinary) => {
      try {
        if (!isBinary) {
          const text = data.toString();
          const payload = JSON.parse(text);

          if (payload.type !== "photo-frame-meta") {
            ws.send(
              JSON.stringify({
                type: "error",
                error: "Unsupported text message type",
              }),
            );
            return;
          }

          const userId = String(payload.userId || "");
          const requestId = String(payload.requestId || "");
          const timestamp = Number(payload.timestamp || Date.now());
          const mimeType = String(payload.mimeType || "image/jpeg");

          if (!userId || !requestId) {
            ws.send(
              JSON.stringify({
                type: "error",
                error: "Missing frame metadata fields",
              }),
            );
            return;
          }

          pendingMetaBySocket.set(ws, {
            userId,
            requestId,
            timestamp,
            mimeType,
          });

          return;
        }

        const meta = pendingMetaBySocket.get(ws);
        if (!meta) {
          ws.send(
            JSON.stringify({
              type: "error",
              error: "Received binary frame without prior metadata",
            }),
          );
          return;
        }

        const buffer = Buffer.isBuffer(data) ? data : Buffer.from(data);

        const frame: LatestFrame = {
          userId: meta.userId,
          requestId: meta.requestId,
          timestamp: meta.timestamp,
          mimeType: meta.mimeType,
          buffer,
        };

        latestFrames.set(meta.userId, frame);
        pendingMetaBySocket.set(ws, null);

        const userDir = path.join(uploadDir, meta.userId);
        fs.mkdirSync(userDir, { recursive: true });
        fs.promises
          .writeFile(path.join(userDir, "latest.jpg"), buffer)
          .catch((err) => console.error("Failed to write latest.jpg:", err));

        ws.send(
          JSON.stringify({
            type: "ack",
            userId: meta.userId,
            requestId: meta.requestId,
            timestamp: meta.timestamp,
            size: buffer.length,
          }),
        );

        const broadcastMessage = JSON.stringify({
          type: "photo-frame",
          userId: meta.userId,
          requestId: meta.requestId,
          timestamp: meta.timestamp,
          dataUrl: frameToDataUrl(frame),
        });

        for (const viewer of viewers) {
          const subscribedUserId = viewerSubscriptions.get(viewer);
          if (
            viewer.readyState === WebSocket.OPEN &&
            subscribedUserId === meta.userId
          ) {
            viewer.send(broadcastMessage);
          }
        }
      } catch (err) {
        console.error("Failed to process publish message:", err);
        ws.send(
          JSON.stringify({
            type: "error",
            error: err instanceof Error ? err.message : "Unknown error",
          }),
        );
      }
    });

    ws.on("close", () => {
      console.log("Publisher disconnected");
      pendingMetaBySocket.delete(ws);

      for (const viewer of viewers) {
        if (viewer.readyState === WebSocket.OPEN) {
          viewer.send(JSON.stringify({ type: "publisher-disconnected" }));
        }
      }
    });

    ws.on("error", (err) => {
      console.error("Publisher socket error:", err);
    });

    return;
  }

  if (url.startsWith("/view")) {
    viewers.add(ws);
    viewerSubscriptions.set(ws, null);
    console.log(`Viewer connected, total viewers=${viewers.size}`);

    ws.send(
      JSON.stringify({
        type: "connected",
        role: "viewer",
      }),
    );

    ws.on("message", (data, isBinary) => {
      try {
        if (isBinary) {
          ws.send(
            JSON.stringify({
              type: "error",
              error: "Viewer endpoint expects JSON messages only",
            }),
          );
          return;
        }

        const text = data.toString();
        const payload = JSON.parse(text);

        if (payload.type !== "subscribe-latest") {
          ws.send(
            JSON.stringify({
              type: "error",
              error: "Unsupported viewer message type",
            }),
          );
          return;
        }

        const userId = String(payload.userId || "");
        if (!userId) {
          ws.send(
            JSON.stringify({
              type: "error",
              error: "Missing userId in subscribe-latest",
            }),
          );
          return;
        }

        viewerSubscriptions.set(ws, userId);

        ws.send(
          JSON.stringify({
            type: "subscribed",
            userId,
          }),
        );

        const latest = latestFrames.get(userId);
        if (latest) {
          ws.send(
            JSON.stringify({
              type: "photo-frame",
              userId: latest.userId,
              requestId: latest.requestId,
              timestamp: latest.timestamp,
              dataUrl: frameToDataUrl(latest),
            }),
          );
        } else {
          ws.send(
            JSON.stringify({
              type: "no-frame-yet",
              userId,
            }),
          );
        }
      } catch (err) {
        console.error("Failed to process viewer message:", err);
        ws.send(
          JSON.stringify({
            type: "error",
            error: err instanceof Error ? err.message : "Unknown error",
          }),
        );
      }
    });

    ws.on("close", () => {
      viewers.delete(ws);
      viewerSubscriptions.delete(ws);
      console.log(`Viewer disconnected, total viewers=${viewers.size}`);
    });

    ws.on("error", (err) => {
      console.error("Viewer socket error:", err);
    });

    return;
  }

  ws.send(JSON.stringify({ type: "error", error: "Unknown endpoint" }));
  ws.close();
});

console.log(`WebSocket relay server running on ws://localhost:${WS_PORT}`);