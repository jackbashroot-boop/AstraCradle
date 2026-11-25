import SwiftUI

struct ContentView: View {
    @EnvironmentObject var client: AstraBeamClient
    @Environment(\.openURL) private var openURL
    @State private var pin: String = ""
    @State private var textInput: String = ""

    var body: some View {
        TabView {
            NavigationStack {
                ScrollView {
                    VStack(spacing: 14) {
                        if client.token.isEmpty {
                            pairCard
                        } else {
                            telemetryCard
                            actionsCard
                            profileCard
                        }
                        if !client.lastMessage.isEmpty {
                            Text(client.lastMessage)
                                .font(.footnote)
                                .foregroundStyle(.mint)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(.horizontal, 4)
                        }
                    }
                    .padding()
                }
                .navigationTitle("AstraCradle")
                .task {
                    await client.refreshSummary()
                }
            }
            .tabItem {
                Label("Control Deck", systemImage: "gauge.with.dots.needle.67percent")
            }

            NavigationStack {
                VStack(spacing: 12) {
                    if let img = client.currentFrame {
                        Image(uiImage: img)
                            .resizable()
                            .scaledToFit()
                            .clipShape(RoundedRectangle(cornerRadius: 14))
                            .gesture(
                                DragGesture(minimumDistance: 0)
                                    .onEnded { val in
                                        Task {
                                            await client.sendInput([
                                                "kind": "click",
                                                "button": "left",
                                                "x": max(0, min(1, val.location.x / 360.0)),
                                                "y": max(0, min(1, val.location.y / 220.0)),
                                            ])
                                        }
                                    }
                            )
                    } else {
                        RoundedRectangle(cornerRadius: 14)
                            .fill(Color.white.opacity(0.06))
                            .frame(height: 220)
                            .overlay(Text("Tap Start Live View to stream PC").foregroundStyle(.secondary))
                    }

                    HStack {
                        Button(client.isStreaming ? "Pause View" : "Start Live View") {
                            if client.isStreaming { client.stopScreenLoop() }
                            else { client.startScreenLoop() }
                        }
                        .buttonStyle(.borderedProminent)
                        Button("Open Moonlight") {
                            if let u = URL(string: "moonlight://") { openURL(u) }
                        }
                        .buttonStyle(.bordered)
                    }

                    HStack {
                        TextField("Type text to PC...", text: $textInput)
                            .textFieldStyle(.roundedBorder)
                        Button("Send") {
                            let txt = textInput
                            textInput = ""
                            Task { await client.sendInput(["kind": "text", "text": txt]) }
                        }
                        .buttonStyle(.borderedProminent)
                    }
                    Spacer()
                }
                .padding()
                .navigationTitle("Screen & Input")
                .onDisappear { client.stopScreenLoop() }
            }
            .tabItem {
                Label("Screen", systemImage: "display")
            }
        }
    }

    private var pairCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Connect iPhone 16 Pro Max").font(.headline)
            TextField("PC Tailscale or LAN IP (e.g. 100.77.94.72)", text: $client.host)
                .keyboardType(.decimalPad)
                .textFieldStyle(.roundedBorder)
            TextField("AstraBeam Port (default 47992)", text: $client.port)
                .keyboardType(.numberPad)
                .textFieldStyle(.roundedBorder)
            SecureField("6-digit AstraBeam PIN", text: $pin)
                .keyboardType(.numberPad)
                .textFieldStyle(.roundedBorder)
            Button("Connect Over Tailscale TLS") {
                Task { await client.pair(pin: pin) }
            }
            .buttonStyle(.borderedProminent)
            .frame(maxWidth: .infinity)
        }
        .padding()
        .background(Color.white.opacity(0.06), in: RoundedRectangle(cornerRadius: 16))
    }

    private var telemetryCard: some View {
        let s = client.summary
        return VStack(alignment: .leading, spacing: 8) {
            HStack {
                VStack(alignment: .leading) {
                    Text(s?.hostname ?? "Desktop PC").font(.title3.bold())
                    Text(s?.active_profile ?? "Balanced").font(.subheadline).foregroundStyle(.cyan)
                }
                Spacer()
                Text("\(s?.health_score ?? 0)")
                    .font(.title.bold())
                    .padding(14)
                    .background(Circle().stroke(.mint, lineWidth: 4))
            }
            HStack {
                statBox("CPU", "\(Int(s?.cpu_percent ?? 0))%")
                statBox("RAM", "\(Int(s?.mem_percent ?? 0))%")
                statBox("GPU", s?.gpu_temp_c != nil ? "\(Int(s!.gpu_temp_c!))°C" : "OK")
            }
        }
        .padding()
        .background(Color.white.opacity(0.06), in: RoundedRectangle(cornerRadius: 16))
    }

    private func statBox(_ title: String, _ val: String) -> some View {
        VStack {
            Text(title).font(.caption).foregroundStyle(.secondary)
            Text(val).font(.headline)
        }
        .frame(maxWidth: .infinity)
        .padding(8)
        .background(Color.white.opacity(0.05), in: RoundedRectangle(cornerRadius: 10))
    }

    private var actionsCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("1-Tap Controls").font(.headline)
            LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 8) {
                Button("Fix Safe") { Task { await client.runAction("fix_safe") } }.buttonStyle(.borderedProminent)
                Button("Deep Scan") { Task { await client.runAction("deep_scan") } }.buttonStyle(.bordered)
                Button("Optimize Wi-Fi") { Task { await client.runAction("optimize_wifi") } }.buttonStyle(.bordered)
                Button("Restart Sunshine") { Task { await client.runAction("restart_sunshine") } }.buttonStyle(.bordered)
                Button("Toggle Shield") { Task { await client.runAction("toggle_shield") } }.buttonStyle(.bordered)
                Button("Pause Shield 5m") { Task { await client.runAction("pause_shield_5m") } }.buttonStyle(.bordered)
            }
        }
        .padding()
        .background(Color.white.opacity(0.06), in: RoundedRectangle(cornerRadius: 16))
    }

    private var profileCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Profiles").font(.headline)
            HStack {
                ForEach(["Balanced Daily", "Gaming / Low-Latency", "Battery Endurance"], id: \.self) { prof in
                    Button(prof.components(separatedBy: " ").first ?? prof) {
                        Task { await client.runAction("switch_profile", value: prof) }
                    }
                    .buttonStyle(.bordered)
                }
            }
        }
        .padding()
        .background(Color.white.opacity(0.06), in: RoundedRectangle(cornerRadius: 16))
    }
}
