# AstraCradle iPhone 16 Pro Max Companion App

## Option 1 — Instant Install (Zero Xcode / No Mac Needed)
1. Open **AstraCradle -> Remote Stream & Devices** on your PC and click **Start Secure Host** (port `47992`).
2. On your **iPhone 16 Pro Max** (on Tailscale or same Wi-Fi), open Safari to:
   `https://<your-PC-Tailscale-or-LAN-IP>:47992/iphone`
3. Tap **Show Details -> visit this website** to accept AstraBeam's local TLS certificate.
4. Tap **Share -> Add to Home Screen** — installs **AstraCradle** with its native icon and Dynamic Island / Safe Area full-screen UI.

## Option 2 — Native SwiftUI Build (Xcode)
1. In Xcode, create a new **iOS App** named `AstraCradleCompanion` (SwiftUI, iOS 17+).
2. Replace `AstraCradleCompanionApp.swift` and `ContentView.swift`, add `AstraBeamClient.swift`, and merge `Info.plist`.
3. Build & run directly onto your **iPhone 16 Pro Max**.
