// Renders a 1024x1024 app-icon PNG (headless CoreGraphics). No window server.
//   swift makeicon.swift <out.png>
import AppKit
import CoreGraphics
import Foundation
import ImageIO
import UniformTypeIdentifiers

let out = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "icon_1024.png"
let S = 1024
let cs = CGColorSpaceCreateDeviceRGB()
guard let ctx = CGContext(data: nil, width: S, height: S, bitsPerComponent: 8,
                          bytesPerRow: 0, space: cs,
                          bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else {
    fatalError("no context")
}
let s = CGFloat(S)

func rrect(_ r: CGRect, _ radius: CGFloat) -> CGPath {
    CGPath(roundedRect: r, cornerWidth: radius, cornerHeight: radius, transform: nil)
}

// Rounded dark "squircle" background with a subtle vertical gradient.
let margin: CGFloat = 76
let bg = CGRect(x: margin, y: margin, width: s - 2*margin, height: s - 2*margin)
ctx.saveGState()
ctx.addPath(rrect(bg, (s - 2*margin) * 0.225))
ctx.clip()
let grad = CGGradient(colorsSpace: cs,
    colors: [CGColor(red: 0.14, green: 0.15, blue: 0.17, alpha: 1),
             CGColor(red: 0.05, green: 0.06, blue: 0.07, alpha: 1)] as CFArray,
    locations: [0, 1])!
ctx.drawLinearGradient(grad, start: CGPoint(x: 0, y: s), end: CGPoint(x: 0, y: 0), options: [])
ctx.restoreGState()

// A rounded "module" outline (the kext), subtle gray.
let box = CGRect(x: s*0.24, y: s*0.22, width: s*0.52, height: s*0.40)
ctx.addPath(rrect(box, 46))
ctx.setStrokeColor(CGColor(red: 0.45, green: 0.47, blue: 0.5, alpha: 0.9))
ctx.setLineWidth(26)
ctx.strokePath()

// A bright green up-arrow (loading the kext into the kernel).
let green = CGColor(red: 0.19, green: 0.82, blue: 0.35, alpha: 1)
let cx = s * 0.5
let stemW = s * 0.11
let stemBottom = s * 0.30
let stemTop = s * 0.55
let headBase = s * 0.50
let headTop = s * 0.78
let headHalf = s * 0.16
let arrow = CGMutablePath()
arrow.move(to: CGPoint(x: cx - stemW/2, y: stemBottom))
arrow.addLine(to: CGPoint(x: cx - stemW/2, y: headBase))
arrow.addLine(to: CGPoint(x: cx - headHalf, y: headBase))
arrow.addLine(to: CGPoint(x: cx, y: headTop))
arrow.addLine(to: CGPoint(x: cx + headHalf, y: headBase))
arrow.addLine(to: CGPoint(x: cx + stemW/2, y: headBase))
arrow.addLine(to: CGPoint(x: cx + stemW/2, y: stemBottom))
arrow.closeSubpath()
ctx.addPath(arrow)
ctx.setFillColor(green)
ctx.fillPath()

guard let img = ctx.makeImage() else { fatalError("no image") }
let url = URL(fileURLWithPath: out)
guard let dest = CGImageDestinationCreateWithURL(url as CFURL, UTType.png.identifier as CFString, 1, nil) else {
    fatalError("no dest")
}
CGImageDestinationAddImage(dest, img, nil)
CGImageDestinationFinalize(dest)
print("wrote \(out)")
