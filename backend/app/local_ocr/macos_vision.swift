import AppKit
import Foundation
import PDFKit
import Vision

struct Output: Codable {
    let text: String
    let pages: Int
}

func recognize(_ image: CGImage) throws -> String {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.recognitionLanguages = ["zh-Hans", "zh-Hant", "en-US"]
    request.usesLanguageCorrection = true
    let handler = VNImageRequestHandler(cgImage: image, options: [:])
    try handler.perform([request])
    let observations = (request.results ?? []).sorted {
        let ay = $0.boundingBox.midY
        let by = $1.boundingBox.midY
        return abs(ay - by) > 0.015 ? ay > by : $0.boundingBox.minX < $1.boundingBox.minX
    }
    return observations.compactMap { $0.topCandidates(1).first?.string }.joined(separator: "\n")
}

func cgImage(_ image: NSImage) -> CGImage? {
    var rect = NSRect(origin: .zero, size: image.size)
    return image.cgImage(forProposedRect: &rect, context: nil, hints: nil)
}

guard CommandLine.arguments.count >= 2 else {
    FileHandle.standardError.write(Data("missing input path\n".utf8))
    exit(2)
}
let source = URL(fileURLWithPath: CommandLine.arguments[1])
let maxPages = CommandLine.arguments.count > 2 ? max(1, Int(CommandLine.arguments[2]) ?? 80) : 80
var pages: [String] = []

do {
    if source.pathExtension.lowercased() == "pdf" {
        guard let document = PDFDocument(url: source) else { throw NSError(domain: "YiyuOCR", code: 10) }
        let count = min(document.pageCount, maxPages)
        for index in 0..<count {
            guard let page = document.page(at: index) else { continue }
            let bounds = page.bounds(for: .mediaBox)
            let scale: CGFloat = 2.0
            let size = NSSize(width: max(1, bounds.width * scale), height: max(1, bounds.height * scale))
            let thumbnail = page.thumbnail(of: size, for: .mediaBox)
            if let image = cgImage(thumbnail) {
                let text = try recognize(image)
                if !text.isEmpty { pages.append("[第 \(index + 1) 页]\n\(text)") }
            }
        }
    } else {
        guard let image = NSImage(contentsOf: source), let value = cgImage(image) else {
            throw NSError(domain: "YiyuOCR", code: 11)
        }
        let text = try recognize(value)
        if !text.isEmpty { pages.append(text) }
    }
    let data = try JSONEncoder().encode(Output(text: pages.joined(separator: "\n\n"), pages: pages.count))
    FileHandle.standardOutput.write(data)
} catch {
    FileHandle.standardError.write(Data("vision ocr failed: \(error.localizedDescription)\n".utf8))
    exit(1)
}
