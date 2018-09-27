from __future__ import division, unicode_literals, print_function, absolute_import
try:
    from labscript_utils import check_version
except ImportError:
    raise ImportError('Require labscript_utils > 2.1.0')
    
check_version('labscript_utils', '2.7.1', '3')
from labscript_utils import PY2
if PY2:
    str = unicode

import os
import sys
import importlib
import imp
import warnings

__version__ = '2.1.0'

check_version('qtutils', '2.0.0', '3.0.0')
check_version('labscript', '2.1', '3')
check_version('blacs', '2.4.0', '3.0.0')
check_version('zprocess', '2.2.7', '3')

from labscript_utils import labscript_suite_install_dir

LABSCRIPT_DEVICES_DIR = os.path.join(labscript_suite_install_dir, 'labscript_devices')

"""This file contains the machinery for registering and looking up what BLACS tab and
runviewer parser classes belong to a particular labscript device. "labscript_device"
here means a device that BLACS needs to communicate with. These devices have
instructions saved within the 'devices' group of the HDF5 file, and have a tab
corresponding to them in the BLACS interface. These device classes must have unique
names, such as "PineBlaster" or "PulseBlaster" etc.

There are two methods we use to find out which BLACS tab and runviewer parser correspond
to a device class: the "old" method, and the "new" method. The old method requires that
the the BLACS tab and runviewer parser be in a file called <DeviceName>.py at the top
level of labscript_devices folder, and that they have class decorators @BLACS_tab or
@runviewer_parser to identify them. This method precludes putting code in subfolders or
splitting it across multiple files.

The "new" method is more flexible. It allows BLACS tabs and runviewer parsers to be
defined in any importable file within a subfolder of labscript_devices. Classes using
this method can be in files with any name, and do not need class decorators. Instead,
the classes should be registered by creating a submodule file called register_classes,
which when imported, makes calls to labscript_devices.register_classes() to tell
labscript_devices which BLACS tab and runviewer parser class belong to each device. Tab
and parser classes must be passed to register_classes() as fully qualified names, i.e.
"labscript_devices.submodule.ClassName", not by passing in the classes themselves. This
ensures imports can be deferred until the classes are actually needed. When BLACS and
runviewer look up classes with get_BLACS_tab() and get_runviewer_parser,
populate_registry() will be called in order to find all files called register_classes.py
within subfolders (at any depth) of labscript_devices, and will import them to run their
code and register their classes.

The "new" method does not impose any restrictions on code organisation within subfolders
of labscript_devices, and so is preferable as it allows auxiliary utilities or resource
files to live in subfolders alongside the device code to which they are relevant, the
use of subrepositories, the grouping of similar devices within subfolders, and other
nice things to have.

The old method may be deprecated in the future.
"""


class ClassRegister(object):
    """A register for looking up classes by module name.  Provides a
     decorator and a method for looking up classes decorated with it,
     importing as necessary."""
    def __init__(self, instancename):
        self.registered_classes = {}
        # The name given to the instance in this namespace, so we can use it in error messages:
        self.instancename = instancename

    def __call__(self, cls):
        """Adds the class to the register so that it can be looked up later
        by module name"""
        # Add an attribute to the class so it knows its own name in case
        # it needs to look up other classes in the same module:
        cls.labscript_device_class_name = cls.__module__.split('.')[-1]
        if cls.labscript_device_class_name == '__main__':
            # User is running the module as __main__. Use the filename instead:
            import __main__
            try:
                cls.labscript_device_class_name = os.path.splitext(os.path.basename(__main__.__file__))[0]
            except AttributeError:
                # Maybe they're running interactively? Or some other funky environment. Either way, we can't proceed.
                raise RuntimeError('Can\'t figure out what the file or module this class is being defined in. ' +
                                   'If you are testing, please test from a more standard environment, such as ' +
                                   'executing a script from the command line, or if you are using an interactive session, ' +
                                   'writing your code in a separate module and importing it.')

        # Add it to the register:
        self.registered_classes[cls.labscript_device_class_name] = cls
        return cls

    def __getitem__(self, name):
        try:
            # Ensure the module's code has run (this does not re-import it if it is already in sys.modules)
            importlib.import_module('.' + name, __name__)
        except ImportError:
            sys.stderr.write('Error importing module %s.%s whilst looking for classes for device %s. '%(__name__, name, name) +
                             'Check that the module exists, is named correctly, and can be imported with no errors. ' +
                             'Full traceback follows:\n')
            raise
        # Class definitions in that module have executed now, check to see if class is in our register:
        try:
            return self.registered_classes[name]
        except KeyError:
            # No? No such class is defined then, or maybe the user forgot to decorate it.
            raise ValueError('No class decorated as a %s found in module %s, '%(self.instancename, __name__ + '.' + name) +
                             'Did you forget to decorate the class definition with @%s?'%(self.instancename))


# Decorating labscript device classes and BLACS worker classes was never used for
# anything and has been deprecated. These decorators can be removed with no ill
# effects. Do nothing, and emit a warning telling the user this.
def deprecated_decorator(name):
    def null_decorator(cls):
        msg = '@%s decorator is unnecessary and can be removed' % name
        warnings.warn(msg, stacklevel=2)
        return cls

    return null_decorator


labscript_device = deprecated_decorator('labscript_device')
BLACS_worker = deprecated_decorator('BLACS_worker')


# These decorators can still be used, but their use will be deprecated in the future
# once all devices in mainline are moved into subfolders with a register_classes.py that
# will play the same role. For the moment we support both mechanisms of registering
# which BLACS tab and runviewer parser class belong to a particular device.
BLACS_tab = ClassRegister('BLACS_tab')
runviewer_parser = ClassRegister('runviewer_parser')


def _import_class_by_fullname(fullname):
    """Import and return a class defined by its fully qualified name as an absolute
    import path, i.e. "module.submodule.ClassName"."""
    split = fullname.split('.')
    module_name = '.'.join(split[:-1])
    class_name = split[-1]
    module = importlib.import_module(module_name)
    return getattr(module, class_name)


# Dictionaries containing the import paths to BLACS tab and runviewer parser classes,
# not the classes themselves. These will be populated by calls to register_classes from
# code within register_classes.py files within subfolders of labscript_devices.
BLACS_tab_registry = {}
runviewer_parser_registry = {}

# Wrapper functions to get devices out of the class registries.
def get_BLACS_tab(name):
    if not BLACS_tab_registry:
        populate_registry()
    if name in BLACS_tab_registry:
        return _import_class_by_fullname(BLACS_tab_registry[name])
    # Fall back on file naming convention + decorator method:
    return BLACS_tab[name]


def get_runviewer_parser(name):
    if not runviewer_parser_registry:
        populate_registry()
    if name in runviewer_parser_registry:
        return _import_class_by_fullname(runviewer_parser_registry[name])
    # Fall back on file naming convention + decorator method:
    return runviewer_parser[name]


def register_classes(labscript_device_name, BLACS_tab=None, runviewer_parser=None):
    """Register the name of the BLACS tab and/or runviewer parser that belong to a
    particular labscript device. labscript_device_name should be a string of just the
    device name, i.e. "DeviceName". BLACS_tab_fullname and runviewer_parser_fullname
    should be strings containing the fully qualified import paths for the BLACS tab and
    runviewer parser classes, such as "labscript_devices.DeviceName.DeviceTab" and
    "labscript_devices.DeviceName.DeviceParser". These need not be in the same module as
    the device class as in this example, but should be within labscript_devices. This
    function should be called from a file called "register_classes.py" within a
    subfolder of labscript_devices. When BLACS or runviewer start up, they will call
    populate_registry(), which will find and run all such files to populate the class
    registries prior to looking up the classes they need"""
    BLACS_tab_registry[labscript_device_name] = BLACS_tab
    runviewer_parser_registry[labscript_device_name] = runviewer_parser


def populate_registry():
    """Walk the labscript_devices folder looking for files called register_classes.py,
    and run them (i.e. import them). These files are expected to make calls to
    register_classes() to inform us of what BLACS tabs and runviewer classes correspond
    to their labscript device classes."""
    for folder, _, filenames in os.walk(LABSCRIPT_DEVICES_DIR):
        if 'register_classes.py' in filenames:
            # The module name is the path to the file, relative to the labscript suite
            # install directory:
            relfolder = os.path.abspath(folder).split(labscript_suite_install_dir, 1)[1]
            module_name = os.path.join(relfolder, 'register_classes').replace(os.path.sep, '.')
            # Open the file using the import machinery, and import it as module_name.
            fp, pathname, desc = imp.find_module('register_classes', [folder])
            module = imp.load_module(module_name, fp, pathname, desc)


if __name__ == '__main__':
    # Ensure code importing labscript_devices gets this module if we're running as
    # __main__, rather than a copy.
    sys.modules['labscript_devices'] = sys.modules['__main__']
