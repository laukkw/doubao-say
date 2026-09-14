"""Apply settings with exact file rollback and explicit recovery failure reporting."""
from dataclasses import dataclass
from pathlib import Path
import stat

from doubao_input.i18n import tr
from doubao_input.settings import config_dir, set_autostart, write_atomic


@dataclass(frozen=True)
class FileSnapshot:
    path: Path
    data: bytes | None
    mode: int

    @classmethod
    def read(cls, path):
        if path.is_symlink():
            raise ValueError(tr("Settings files must not be symbolic links.", "设置文件不能是符号链接。"))
        if path.exists():
            return cls(path, path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
        return cls(path, None, 0o600)

    def restore(self):
        if self.data is None:
            self.path.unlink(missing_ok=True)
        else:
            if (self.path.is_file() and not self.path.is_symlink()
                    and self.path.read_bytes() == self.data
                    and stat.S_IMODE(self.path.stat().st_mode) == self.mode):
                return
            write_atomic(self.path, self.data, self.mode)


def apply_preferences(previous, proposed, apply_runtime, restore_runtime):
    """Rollback handled failures; this is not a crash-atomic multi-file transaction."""
    proposed.validate()
    files = [FileSnapshot.read(config_dir() / "doubao-say/settings.json"),
             FileSnapshot.read(config_dir() / "autostart/doubao-say.desktop")]
    runtime_attempted = False
    try:
        proposed.save()
        set_autostart(proposed.autostart)
        runtime_attempted = True
        apply_runtime(proposed)
    except Exception as error:
        recovery_errors = []
        if runtime_attempted:
            try:
                restore_runtime(previous)
            except Exception as rollback_error:
                recovery_errors.append(rollback_error)
        for snapshot in reversed(files):
            try:
                snapshot.restore()
            except Exception as rollback_error:
                recovery_errors.append(rollback_error)
        if recovery_errors:
            raise OSError(tr("Settings failed and recovery was incomplete. Reopen the app and check settings and autostart.",
                             "设置失败且未能完整恢复，请重新打开应用并检查设置和自启动。")) from error
        raise
