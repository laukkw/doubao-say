import QtQuick
import Quickshell
import Quickshell.Io

Item {
  id: root

  readonly property string launcher: decodeURIComponent(Qt.resolvedUrl("../start.sh")
    .toString().replace("file://", ""))
  property bool shuttingDown: false

  Process {
    id: daemon
    command: [root.launcher, "--background"]
    running: true
    stdout: SplitParser {
      splitMarker: "\n"
      onRead: function(line) { console.log("Doubao Say:", line) }
    }
    stderr: SplitParser {
      splitMarker: "\n"
      onRead: function(line) { console.warn("Doubao Say:", line) }
    }

    onExited: function(exitCode) {
      restartTimer.interval = exitCode === 0 ? 15000 : 3000
      if (!root.shuttingDown) restartTimer.start()
    }
  }

  Timer {
    id: restartTimer
    interval: 3000
    repeat: false
    onTriggered: daemon.running = true
  }

  Component.onDestruction: {
    root.shuttingDown = true
    restartTimer.stop()
    daemon.running = false
  }
}
