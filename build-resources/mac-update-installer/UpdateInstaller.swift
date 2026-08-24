import AppKit
import Foundation

private let productName = "益语智库AI（新版）"
private let productBundleID = "com.yiyu.thinktank.strict"

private struct BundleIdentity {
    let bundleID: String
    let version: String
}

private enum InstallFailure: LocalizedError {
    case message(String)

    var errorDescription: String? {
        switch self {
        case .message(let value): return value
        }
    }
}

private func identity(at appURL: URL) -> BundleIdentity? {
    let infoURL = appURL.appendingPathComponent("Contents/Info.plist")
    guard let info = NSDictionary(contentsOf: infoURL),
          let bundleID = info["CFBundleIdentifier"] as? String,
          let version = info["CFBundleShortVersionString"] as? String else { return nil }
    return BundleIdentity(bundleID: bundleID, version: version)
}

private func run(_ executable: String, _ arguments: [String]) throws -> String {
    let process = Process()
    let output = Pipe()
    let errors = Pipe()
    process.executableURL = URL(fileURLWithPath: executable)
    process.arguments = arguments
    process.standardOutput = output
    process.standardError = errors
    try process.run()
    process.waitUntilExit()
    let stdout = String(data: output.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
    let stderr = String(data: errors.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
    guard process.terminationStatus == 0 else {
        throw InstallFailure.message(stderr.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? "系统安装操作失败。" : stderr.trimmingCharacters(in: .whitespacesAndNewlines))
    }
    return stdout
}

private func shellQuote(_ value: String) -> String {
    return "'" + value.replacingOccurrences(of: "'", with: "'\\''") + "'"
}

private func appleScriptQuote(_ value: String) -> String {
    return "\"" + value
        .replacingOccurrences(of: "\\", with: "\\\\")
        .replacingOccurrences(of: "\"", with: "\\\"") + "\""
}

private func runWithAdministratorPrivileges(_ command: String) throws {
    _ = try run("/usr/bin/osascript", [
        "-e",
        "do shell script \(appleScriptQuote(command)) with administrator privileges",
    ])
}

private func dockApplicationURLs() -> [URL] {
    guard let data = try? run("/usr/bin/defaults", ["export", "com.apple.dock", "-"]).data(using: .utf8),
          let root = try? PropertyListSerialization.propertyList(from: data, options: [], format: nil) as? [String: Any],
          let applications = root["persistent-apps"] as? [[String: Any]] else {
        return []
    }
    return applications.compactMap { item in
        guard let tile = item["tile-data"] as? [String: Any],
              let fileData = tile["file-data"] as? [String: Any],
              let raw = fileData["_CFURLString"] as? String,
              let url = URL(string: raw), url.isFileURL else { return nil }
        return url.standardizedFileURL
    }
}

private func existingProductURL(from candidates: [URL]) -> URL? {
    var seen = Set<String>()
    for candidate in candidates {
        let path = candidate.standardizedFileURL.path
        guard seen.insert(path).inserted else { continue }
        guard let found = identity(at: candidate), found.bundleID == productBundleID else { continue }
        return candidate
    }
    return nil
}

private func stopRunningProduct() {
    let running = NSRunningApplication.runningApplications(withBundleIdentifier: productBundleID)
    for application in running { application.terminate() }
    let deadline = Date().addingTimeInterval(12)
    while Date() < deadline {
        if NSRunningApplication.runningApplications(withBundleIdentifier: productBundleID).isEmpty { return }
        Thread.sleep(forTimeInterval: 0.2)
    }
    for application in NSRunningApplication.runningApplications(withBundleIdentifier: productBundleID) {
        application.forceTerminate()
    }
    Thread.sleep(forTimeInterval: 0.5)
}

private func replaceProduct(payload: URL, target: URL, expectedVersion: String) throws {
    let parent = target.deletingLastPathComponent()
    let token = UUID().uuidString
    let staged = parent.appendingPathComponent(".\(productName).update-\(token).app")
    let backup = parent.appendingPathComponent(".\(productName).previous-\(token).app")
    let manager = FileManager.default

    let directInstall = manager.isWritableFile(atPath: parent.path)
    if directInstall {
        try? manager.removeItem(at: staged)
        _ = try run("/usr/bin/ditto", [payload.path, staged.path])
        if manager.fileExists(atPath: target.path) { try manager.moveItem(at: target, to: backup) }
        do {
            try manager.moveItem(at: staged, to: target)
            _ = try? run("/usr/bin/xattr", ["-dr", "com.apple.quarantine", target.path])
            guard let installed = identity(at: target), installed.bundleID == productBundleID, installed.version == expectedVersion else {
                throw InstallFailure.message("新版写入后身份或版本校验失败。")
            }
            try? manager.removeItem(at: backup)
        } catch {
            try? manager.removeItem(at: target)
            if manager.fileExists(atPath: backup.path) { try? manager.moveItem(at: backup, to: target) }
            throw error
        }
        return
    }

    let command = [
        "set -eu",
        "/bin/rm -rf \(shellQuote(staged.path)) \(shellQuote(backup.path))",
        "/usr/bin/ditto \(shellQuote(payload.path)) \(shellQuote(staged.path))",
        "if [ -e \(shellQuote(target.path)) ] || [ -L \(shellQuote(target.path)) ]; then /bin/mv \(shellQuote(target.path)) \(shellQuote(backup.path)); fi",
        "if /bin/mv \(shellQuote(staged.path)) \(shellQuote(target.path)); then /usr/bin/xattr -dr com.apple.quarantine \(shellQuote(target.path)) 2>/dev/null || true; /bin/rm -rf \(shellQuote(backup.path)); else [ ! -e \(shellQuote(target.path)) ] && [ -e \(shellQuote(backup.path)) ] && /bin/mv \(shellQuote(backup.path)) \(shellQuote(target.path)); exit 71; fi",
    ].joined(separator: "; ")
    try runWithAdministratorPrivileges(command)
    guard let installed = identity(at: target), installed.bundleID == productBundleID, installed.version == expectedVersion else {
        throw InstallFailure.message("新版写入后身份或版本校验失败。")
    }
}

private func alert(title: String, message: String, button: String = "确定") -> NSApplication.ModalResponse {
    let panel = NSAlert()
    panel.messageText = title
    panel.informativeText = message
    panel.alertStyle = title.contains("失败") ? .critical : .informational
    panel.addButton(withTitle: button)
    return panel.runModal()
}

private func install(nonInteractive: Bool, verificationTarget: URL? = nil) throws -> URL {
    guard let resources = Bundle.main.resourceURL else {
        throw InstallFailure.message("安装程序缺少资源目录。")
    }
    let payload = resources.appendingPathComponent("Payload").appendingPathComponent("\(productName).app")
    guard let payloadIdentity = identity(at: payload), payloadIdentity.bundleID == productBundleID else {
        throw InstallFailure.message("安装程序中的软件身份不正确。")
    }
    _ = try run("/usr/bin/codesign", ["--verify", "--deep", "--strict", payload.path])

    let home = FileManager.default.homeDirectoryForCurrentUser
    let known = [
        URL(fileURLWithPath: "/Applications/\(productName).app"),
        home.appendingPathComponent("Applications/\(productName).app"),
    ]
    let dockMatches = dockApplicationURLs().filter {
        identity(at: $0)?.bundleID == productBundleID
    }
    if let verificationTarget {
        guard verificationTarget.lastPathComponent == "\(productName).app" else {
            throw InstallFailure.message("隔离覆盖验证目标名称不正确。")
        }
        if FileManager.default.fileExists(atPath: verificationTarget.path),
           identity(at: verificationTarget)?.bundleID != productBundleID {
            throw InstallFailure.message("隔离覆盖验证目标不是本软件。")
        }
    }
    let target = verificationTarget
        ?? existingProductURL(from: dockMatches + known)
        ?? home.appendingPathComponent("Applications/\(productName).app")

    if !nonInteractive {
        let current = identity(at: target)?.version ?? "未安装"
        let response = NSAlert()
        response.messageText = "安装或更新\(productName)"
        response.informativeText = "点击“安装并打开”后，\(current) 会自动退出并原位更新为 \(payloadIdentity.version)。原扩展坞图标保持有效，无需拖拽或手动删除旧版。"
        response.addButton(withTitle: "安装并打开")
        response.addButton(withTitle: "取消")
        if response.runModal() != .alertFirstButtonReturn {
            throw InstallFailure.message("用户取消安装。")
        }
    }

    stopRunningProduct()
    try FileManager.default.createDirectory(at: target.deletingLastPathComponent(), withIntermediateDirectories: true)
    try replaceProduct(payload: payload, target: target, expectedVersion: payloadIdentity.version)
    if verificationTarget == nil {
        NSWorkspace.shared.open(target)
    }
    return target
}

let application = NSApplication.shared
application.setActivationPolicy(.regular)
application.activate(ignoringOtherApps: true)
let nonInteractive = CommandLine.arguments.contains("--noninteractive")
let verificationTarget = CommandLine.arguments
    .first(where: { $0.hasPrefix("--verification-target=") })
    .map { URL(fileURLWithPath: String($0.dropFirst("--verification-target=".count))) }
do {
    _ = try install(nonInteractive: nonInteractive, verificationTarget: verificationTarget)
    if !nonInteractive {
        _ = alert(title: "更新完成", message: "已更新至新版，\(productName) 正在重新打开。")
    }
    exit(EXIT_SUCCESS)
} catch {
    let message = (error as? LocalizedError)?.errorDescription ?? String(describing: error)
    if !nonInteractive && message != "用户取消安装。" {
        _ = alert(title: "安装失败", message: message)
    }
    fputs("\(message)\n", stderr)
    exit(message == "用户取消安装。" ? EXIT_SUCCESS : EXIT_FAILURE)
}
