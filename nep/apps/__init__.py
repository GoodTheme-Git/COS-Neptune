"""Neptune program registry: key -> (class, display name, icon[, 'hidden'])."""
from .dialogs import About, CrashDialog, PowerDialog, Run, Calendar, SysInfo, Help, MessageBox
from .text import Notepad, Terminal
from .files import Explorer, Photos
from .media import Calculator, Paint, Personalize
from .taskmgr import TaskManager
from .druman import Druman
from .plugins import AppManager
from .lockscreen import LockSettings
from .pkgapps import InstallWizard, ProgramsFeatures, DeviceManager, CosMode
from ..compat.host import CompatSettings, CosHost
from .webinator import Webinator
from .pigeon import Pigeon
from .archiver import Archiver, ExtractDialog, CompressDialog, ArchiveProgress
from .saverset import SaverSettings
from .ceap import CeapMain
from .control import ControlPanel
from .webceap import CeapWeb
from .ceapmaker import Maker as CeapMaker
from .recycle import RecycleBin
from .cosplace_app import COSPlace
from .cities_app import COSCities
from .tcfnet import TCFNet
from .background import Background

REGISTRY = {
    'files': (Explorer, 'Explorer', 'folder'),
    'notepad': (Notepad, 'Notepad', 'notepad'),
    'calc': (Calculator, 'Calculator', 'calc'),
    'terminal': (Terminal, 'Terminal', 'terminal'),
    'paint': (Paint, 'Paint', 'paint'),
    'photos': (Photos, 'Photo Viewer', 'image'),
    'personalize': (Personalize, 'Personalize', 'personalize'),
    'sysinfo': (SysInfo, 'System Information', 'computer'),
    'help': (Help, 'Help and Support', 'help'),
    'about': (About, 'About COS Neptune', 'neptune'),
    'calendar': (Calendar, 'Date and Time', 'clock'),
    'run': (Run, 'Run', 'terminal'),
    'taskmgr': (TaskManager, 'Task Manager', 'taskmgr'),
    'druman': (Druman, 'Druman', 'druman'),
    'appmgr': (AppManager, 'App Manager', 'plugin'),
    'lockset': (LockSettings, 'Lock Screen', 'lock'),
    'webinator': (Webinator, 'Webinator', 'webinator'),
    'pigeon': (Pigeon, 'Pigeon', 'pigeon'),
    'archiver': (Archiver, 'Archiver', 'archive'),
    'saverset': (SaverSettings, 'Screen Saver', 'saver'),
    'ceap': (CeapMain, 'CEAP', 'ceap'),
    'control': (ControlPanel, 'Control Panel', 'settings'),
    'ceapweb': (CeapWeb, 'CEAP:Web', 'webinator'),
    'ceapmaker': (CeapMaker, 'CEAP App Maker', 'ceap'),
    'recycle': (RecycleBin, 'Recycle Bin', 'recycle'),
    'cosplace': (COSPlace, 'The COS Place', 'cosplace'),
    'cities': (COSCities, 'COS Cities', 'cities'),
    'extract': (ExtractDialog, 'Extract', 'archive', 'hidden'),
    'compress': (CompressDialog, 'Compress', 'archive', 'hidden'),
    'archivejob': (ArchiveProgress, 'Working', 'archive', 'hidden'),
    'tcfnet': (TCFNet, 'TCFNet', 'tcfnet'),
    'background': (Background, 'Desktop Background', 'pictures'),
    'programs': (ProgramsFeatures, 'Programs and Features', 'package'),
    'devmgr': (DeviceManager, 'Device Manager', 'devmgr'),
    'cosmode': (CosMode, 'COS v2.4 Mode', 'cosmode'),
    'installer': (InstallWizard, 'Install Package', 'package', 'hidden'),
    'compat': (CompatSettings, 'Compatibility', 'cosapp', 'hidden'),
    'cosrun': (CosHost, 'COS Program', 'cosapp', 'hidden'),
    'powerdlg': (PowerDialog, 'Shut Down', 'power', 'hidden'),
    'crash': (CrashDialog, 'Runtime Error', 'error', 'hidden'),
}
