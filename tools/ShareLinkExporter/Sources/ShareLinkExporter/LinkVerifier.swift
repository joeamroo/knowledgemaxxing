import Foundation

/// Confirms a captured share URL actually resolves before it is accepted.
/// A failed verification makes the whole attempt fail, which triggers the
/// engine's retry and regenerates the link.
struct LinkVerifier {
    let timeoutSeconds: Double

    func verify(_ urlString: String) async -> Bool {
        guard let url = URL(string: urlString) else { return false }
        var request = URLRequest(url: url, timeoutInterval: timeoutSeconds)
        // GET, not HEAD: some CDN configurations reject HEAD with 405.
        request.setValue(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15",
            forHTTPHeaderField: "User-Agent"
        )
        do {
            let (_, response) = try await URLSession.shared.data(for: request)
            guard let http = response as? HTTPURLResponse else { return false }
            return (200...299).contains(http.statusCode)
        } catch {
            return false
        }
    }
}
