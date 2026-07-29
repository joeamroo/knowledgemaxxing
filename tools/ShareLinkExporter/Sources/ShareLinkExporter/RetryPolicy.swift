import Foundation

/// Simple bounded-retry helper with growing pauses between attempts.
struct RetryPolicy {
    let maxAttempts: Int
    let baseDelayMs: Int

    /// Runs `attempt` up to maxAttempts times until it returns a value.
    /// `onRetry` fires before each re-attempt (used to reset UI state).
    func run<T>(
        attempt: () async -> T?,
        onRetry: (Int) async -> Void
    ) async -> T? {
        for attemptNumber in 1...maxAttempts {
            if let value = await attempt() { return value }
            if attemptNumber < maxAttempts {
                await onRetry(attemptNumber)
                let delay = baseDelayMs * attemptNumber
                try? await Task.sleep(for: .milliseconds(delay))
            }
        }
        return nil
    }
}
