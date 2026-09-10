# SPDX-License-Identifier: GPL-3.0-or-later
"""AI Retopo — KI-Retopologie über die Scenario API (Modelle in models.json).

Port des Retopology-Tools aus der Phototron Desktop-App als Blender-Add-on.
Das gewählte Mesh wird im Object Mode hochgeladen, retopologisiert und als
neues Objekt an der Position des Originals wieder importiert.
"""

from . import preferences, props, history, operators, panel

# history nach props: sein register() spiegelt die Historie in die Liste, die
# props am WindowManager anlegt
_modules = (preferences, props, history, operators, panel)


def register():
    for mod in _modules:
        mod.register()


def unregister():
    for mod in reversed(_modules):
        mod.unregister()
