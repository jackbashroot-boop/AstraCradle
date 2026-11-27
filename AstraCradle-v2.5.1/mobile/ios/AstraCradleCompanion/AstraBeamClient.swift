import Foundation
import SwiftUI

struct WatchSummary: Decodable {
    let ok: Bool
    let hostname: String?
    let health_score: Int?
    let cpu_percent: Double?
    let mem_percent: Double?
    let cpu_temp_c: Double?
    let gpu_temp_c: Double?
    let active_profile: String?
    let astrashield_running: Bool?
    let sunshine_running: Bool?
    let tailscale_ip: String?
    let wifi_ssid: String?
    let wifi_channel: Int?
}

@MainActor
final class AstraBeamClient: NSObject, ObservableObject, URLSessionDelegate {
    @Published var host: String = UserDefaults.standard.string(forKey: "astra_host") ?? ""
    @Published var port: String = UserDefaults.standard.string(forKey: "astra_port") ?? "47992"
    @Published var token: String = UserDefaults.standard.string(forKey: "astra_token") ?? ""
    @Published var summary: WatchSummary?
    @Published var lastMessage: String = ""
    @Published var currentFrame: UIImage?
    @Published var isStreaming: Bool = false

    private lazy var session: URLSession = {
        let cfg = URLSessionConfiguration.default
        cfg.timeoutIntervalForRequest = 6
        return URLSession(configuration: cfg, delegate: self, delegateQueue: nil)
    }()

    private var frameTimer: Timer?

    // Trust AstraBeam's self-signed Tailscale TLS certificate on the configured host
    nonisolated func urlSession(
        _ session: URLSession,
        didReceive challenge: URLAuthenticationChallenge,
        completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void
    ) {
        if challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
           let trust = challenge.protectionSpace.serverTrust {
            completionHandler(.useCredential, URLCredential(trust: trust))
            return
        }
        completionHandler(.performDefaultHandling, nil)
    }

    var baseURL: String {
        let h = host.trimmingCharacters(in: .whitespacesAndNewlines)
        let p = port.trimmingCharacters(in: .whitespacesAndNewlines)
        return "https://\(h):\(p.isEmpty ? "47992" : p)"
    }

    func saveHost() {
        UserDefaults.standard.set(host, forKey: "astra_host")
        UserDefaults.standard.set(port, forKey: "astra_port")
    }

    func pair(pin: String) async {
        saveHost()
        guard let url = URL(string: "\(baseURL)/api/pair") else {
            lastMessage = "Invalid host or port."
            return
        }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONSerialization.data(withJSONObject: ["pin": pin])
        do {
            let (data, _) = try await session.data(for: req)
            if let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let ok = obj["ok"] as? Bool, ok,
               let tok = obj["token"] as? String {
                token = tok
                UserDefaults.standard.set(tok, forKey: "astra_token")
                lastMessage = "Paired over Tailscale TLS."
                await refreshSummary()
            } else {
                let err = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["error"] as? String
                lastMessage = err ?? "Invalid PIN."
            }
        } catch {
            lastMessage = "Connection failed: \(error.localizedDescription)"
        }
    }

    func refreshSummary() async {
        guard !token.isEmpty, let url = URL(string: "\(baseURL)/api/watch/summary") else { return }
        var req = URLRequest(url: url)
        req.setValue(token, forHTTPHeaderField: "X-AstraBeam-Token")
        do {
            let (data, resp) = try await session.data(for: req)
            if let http = resp as? HTTPURLResponse, http.statusCode == 401 {
                token = ""
                UserDefaults.standard.removeObject(forKey: "astra_token")
                lastMessage = "Session expired — enter PIN again."
                return
            }
            summary = try JSONDecoder().decode(WatchSummary.self, from: data)
        } catch {
            lastMessage = "Status error: \(error.localizedDescription)"
        }
    }

    func runAction(_ action: String, value: String = "") async {
        guard !token.isEmpty, let url = URL(string: "\(baseURL)/api/watch/action") else { return }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.setValue(token, forHTTPHeaderField: "X-AstraBeam-Token")
        req.httpBody = try? JSONSerialization.data(withJSONObject: ["action": action, "value": value])
        do {
            let (data, _) = try await session.data(for: req)
            if let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let msg = obj["message"] as? String {
                lastMessage = msg
            }
            await refreshSummary()
        } catch {
            lastMessage = "Action error: \(error.localizedDescription)"
        }
    }

    func sendInput(_ payload: [String: Any]) async {
        guard !token.isEmpty, let url = URL(string: "\(baseURL)/api/input") else { return }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.setValue(token, forHTTPHeaderField: "X-AstraBeam-Token")
        req.httpBody = try? JSONSerialization.data(withJSONObject: payload)
        _ = try? await session.data(for: req)
    }

    func startScreenLoop() {
        stopScreenLoop()
        isStreaming = true
        frameTimer = Timer.scheduledTimer(withTimeInterval: 0.12, repeats: true) { [weak self] _ in
            Task { @MainActor in
                await self?.fetchSingleFrame()
            }
        }
    }

    func stopScreenLoop() {
        isStreaming = false
        frameTimer?.invalidate()
        frameTimer = nil
    }

    private func fetchSingleFrame() async {
        guard !token.isEmpty, let url = URL(string: "\(baseURL)/api/frame") else { return }
        var req = URLRequest(url: url)
        req.setValue(token, forHTTPHeaderField: "X-AstraBeam-Token")
        if let (data, _) = try? await session.data(for: req),
           let img = UIImage(data: data) {
            currentFrame = img
        }
    }
}
