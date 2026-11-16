import AppKit
import WebKit
import Foundation

func fail(_ message: String) -> Never {
    fputs("SVG renderer: \(message)\n", stderr)
    exit(1)
}

guard CommandLine.arguments.count == 3 else {
    fail("Usage: swift svg-to-png.swift input.svg output.png")
}

let input = URL(fileURLWithPath: CommandLine.arguments[1])
let output = URL(fileURLWithPath: CommandLine.arguments[2])
guard FileManager.default.isReadableFile(atPath: input.path) else {
    fail("Input SVG is not readable: \(input.path)")
}

let width = 1280
let height = 640
let app = NSApplication.shared
app.setActivationPolicy(.prohibited)
let webView = WKWebView(frame: NSRect(x: 0, y: 0, width: width, height: height))
let delegate = RendererDelegate(output: output, width: width, height: height)
webView.navigationDelegate = delegate
let deadline = Timer.scheduledTimer(withTimeInterval: 30, repeats: false) { _ in
    webView.stopLoading()
    fail("Rendering timed out after 30 seconds")
}
webView.loadFileURL(input, allowingReadAccessTo: input.deletingLastPathComponent())
app.run()

final class RendererDelegate: NSObject, WKNavigationDelegate {
    let output: URL
    let width: Int
    let height: Int

    init(output: URL, width: Int, height: Int) {
        self.output = output
        self.width = width
        self.height = height
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        fail("SVG navigation failed: \(error.localizedDescription)")
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        fail("Unable to load SVG: \(error.localizedDescription)")
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        fail("WebKit content process terminated before rendering completed")
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        let config = WKSnapshotConfiguration()
        config.rect = CGRect(x: 0, y: 0, width: width, height: height)
        config.snapshotWidth = NSNumber(value: width)
        webView.takeSnapshot(with: config) { image, error in
            guard let image = image,
                  let tiff = image.tiffRepresentation,
                  let bitmap = NSBitmapImageRep(data: tiff),
                  let png = bitmap.representation(using: .png, properties: [:]) else {
                fail("PNG conversion failed: \(String(describing: error))")
            }
            do {
                try png.write(to: self.output, options: .atomic)
            } catch {
                fail("Unable to write PNG: \(error.localizedDescription)")
            }
            deadline.invalidate()
            print("Generated: \(self.output.path)")
            exit(0)
        }
    }
}
