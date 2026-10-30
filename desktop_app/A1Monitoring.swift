import Cocoa

// A local launcher: the existing host runner retains all scan and safety gates.
if CommandLine.arguments.count == 3 && CommandLine.arguments[1] == "--make-icon" {
    let directory = URL(fileURLWithPath: CommandLine.arguments[2])
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    for (name, pixels) in [("icon_16x16",16),("icon_16x16@2x",32),("icon_32x32",32),
                           ("icon_32x32@2x",64),("icon_128x128",128),("icon_128x128@2x",256),
                           ("icon_256x256",256),("icon_256x256@2x",512),
                           ("icon_512x512",512),("icon_512x512@2x",1024)] {
        let bitmap = NSBitmapImageRep(bitmapDataPlanes:nil, pixelsWide:pixels, pixelsHigh:pixels,
            bitsPerSample:8, samplesPerPixel:4, hasAlpha:true, isPlanar:false,
            colorSpaceName:.deviceRGB, bytesPerRow:0, bitsPerPixel:0)!
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep:bitmap)
        NSGraphicsContext.current!.cgContext.scaleBy(x:CGFloat(pixels)/1024, y:CGFloat(pixels)/1024)
        NSColor(calibratedRed:0.12, green:0.40, blue:0.34, alpha:1).setFill()
        NSBezierPath(roundedRect:NSRect(x:40,y:40,width:944,height:944), xRadius:210, yRadius:210).fill()
        let attributes:[NSAttributedString.Key:Any] = [.font:NSFont.systemFont(ofSize:340,weight:.bold),
                                                       .foregroundColor:NSColor.white]
        ("A1" as NSString).draw(at:NSPoint(x:200,y:420), withAttributes:attributes)
        NSColor(calibratedRed:0.70, green:0.93, blue:0.69, alpha:1).setStroke()
        let line = NSBezierPath()
        line.lineWidth = 42
        line.lineCapStyle = .round
        line.lineJoinStyle = .round
        line.move(to:NSPoint(x:225,y:285)); line.line(to:NSPoint(x:405,y:285))
        line.line(to:NSPoint(x:520,y:375)); line.line(to:NSPoint(x:655,y:375))
        line.line(to:NSPoint(x:800,y:490)); line.stroke()
        let arrow = NSBezierPath()
        arrow.lineWidth = 42; arrow.lineCapStyle = .round; arrow.lineJoinStyle = .round
        arrow.move(to:NSPoint(x:690,y:490)); arrow.line(to:NSPoint(x:800,y:490))
        arrow.line(to:NSPoint(x:800,y:380)); arrow.stroke()
        NSGraphicsContext.restoreGraphicsState()
        try bitmap.representation(using:.png, properties:[:])!.write(to:directory.appendingPathComponent(name+".png"))
    }
    exit(0)
}

final class Launcher: NSObject, NSApplicationDelegate {
    var window:NSWindow!
    let project = Bundle.main.object(forInfoDictionaryKey:"MonitoringProjectPath") as! String

    func applicationDidFinishLaunching(_ notification:Notification) {
        let menu = NSMenu()
        let item = NSMenuItem(); menu.addItem(item)
        let appMenu = NSMenu(); item.submenu = appMenu
        appMenu.addItem(withTitle:"Quit A1 Monitoring", action:#selector(NSApplication.terminate(_:)), keyEquivalent:"q")
        NSApp.mainMenu = menu
        window = NSWindow(contentRect:NSRect(x:0,y:0,width:440,height:350),
                          styleMask:[.titled,.closable,.miniaturizable], backing:.buffered, defer:false)
        window.title = "A1 Monitoring"
        window.isReleasedWhenClosed = false
        let stack = NSStackView(); stack.orientation = .vertical
        stack.alignment = .centerX; stack.spacing = 14
        stack.translatesAutoresizingMaskIntoConstraints = false
        let title = NSTextField(labelWithString:"A1 Monitoring")
        title.font = .systemFont(ofSize:28,weight:.bold)
        let subtitle = NSTextField(labelWithString:"Auto.ru · Avito")
        subtitle.font = .systemFont(ofSize:16); subtitle.textColor = .secondaryLabelColor
        stack.addArrangedSubview(title); stack.addArrangedSubview(subtitle)
        for (text, action) in [("Start monitoring",#selector(start)),
                               ("Continue unfinished checks",#selector(resume)),
                               ("Open latest report",#selector(report))] {
            let button = NSButton(title:text, target:self, action:action)
            button.bezelStyle = .rounded; button.font = .systemFont(ofSize:16,weight:.medium)
            button.translatesAutoresizingMaskIntoConstraints = false
            button.widthAnchor.constraint(equalToConstant:340).isActive = true
            button.heightAnchor.constraint(equalToConstant:40).isActive = true
            stack.addArrangedSubview(button)
        }
        let footer = NSTextField(wrappingLabelWithString:"Keep your Mac awake and unlocked.\nSolve CAPTCHA in the monitoring Chrome window.")
        footer.font = .systemFont(ofSize:12); footer.textColor = .secondaryLabelColor
        footer.alignment = .center; stack.addArrangedSubview(footer)
        window.contentView!.addSubview(stack)
        NSLayoutConstraint.activate([stack.centerXAnchor.constraint(equalTo:window.contentView!.centerXAnchor),
            stack.centerYAnchor.constraint(equalTo:window.contentView!.centerYAnchor)])
        window.center(); window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps:true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender:NSApplication) -> Bool { true }
    @objc func start() { launch("Start Monitoring.command") }
    @objc func resume() { launch("Continue Monitoring.command") }
    @objc func report() {
        NSWorkspace.shared.open(URL(string:Bundle.main.object(forInfoDictionaryKey:"MonitoringReportURL") as! String)!)
    }
    func launch(_ name:String) {
        let file = URL(fileURLWithPath:project).appendingPathComponent(name)
        if !FileManager.default.isExecutableFile(atPath:file.path) || !NSWorkspace.shared.open(file) {
            let alert = NSAlert(); alert.messageText = "Monitoring launcher is unavailable"
            alert.informativeText = "Check that the project remains at \(project)."; alert.runModal()
        }
    }
}
let application = NSApplication.shared
let delegate = Launcher()
application.setActivationPolicy(.regular)
application.delegate = delegate
application.run()
