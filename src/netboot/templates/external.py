"""Engines that render through another language's own tooling.

ERB and EPP are Ruby and Puppet templates; neither has a faithful Python
implementation, and a half-faithful one is worse than none for a file someone
already has. So the template and the context go to the real renderer: the
context is marshalled to JSON, the program is run as a subprocess, and its
stdout is the artifact.

The cost is explicit: a render spawns a process, needs the program installed, and
the template is executed by that language with whatever the context holds. A
template is already code in every engine here, so that is not a new trust
boundary -- but it is someone else's interpreter, so the program is named per
engine and overridable by environment variable.
"""

import json
import os
import shutil
import subprocess
import tempfile
from typing import ClassVar, Tuple

from pathlib_next import Path

from .common import DEFAULT_PRIORITY, Template, TemplateEngineError, can_process_suffix
from .data import jsonable, template_data


class SubprocessTemplate(Template):
    """Base for an engine that shells out to another language's renderer.

    A subclass sets `COMMAND` (the program), `ENV_VAR` (an override naming the
    program's path), `EXT`, and `command()` to build the argument list.
    """

    #: The program to run, looked up on `PATH` unless it is a path already.
    COMMAND: ClassVar[str] = ""
    #: Environment variable overriding `COMMAND`, for a program that is installed
    #: but not on `PATH`.
    ENV_VAR: ClassVar[str] = ""
    #: Seconds before a render is abandoned. A template that waits for something
    #: must not hang a provisioning run.
    TIMEOUT: ClassVar[int] = 60
    #: Basename the template is written under, so the program's own error
    #: messages name something recognisable.
    BASENAME: ClassVar[str] = "template"
    #: Filename for the marshalled context. The contents are always JSON; the
    #: name matters because a program may insist on an extension -- `puppet epp
    #: render` refuses a `--values_file` that is not `.yaml` or `.pp`, and JSON
    #: is valid YAML.
    VALUES_NAME: ClassVar[str] = "values.json"

    PRIORITY = DEFAULT_PRIORITY

    def __init__(self, template: str) -> None:
        self._source = template
        # Fail at load time, while the file that needs the program is the
        # obvious subject, rather than part-way through a provisioning run.
        self.executable()

    @classmethod
    def executable(cls) -> str:
        """The program's full path, or a `TemplateEngineError` saying it is missing."""
        name = (os.environ.get(cls.ENV_VAR) if cls.ENV_VAR else None) or cls.COMMAND
        found = shutil.which(name) if not os.path.isabs(name) else name
        if not found or not os.path.exists(found):
            raise TemplateEngineError(
                f"the {cls.__name__.replace('Template', '').lower()} engine needs "
                f"{cls.COMMAND!r} installed and on PATH"
                + (f"; set {cls.ENV_VAR} to its path instead" if cls.ENV_VAR else "")
            )
        return found

    def command(self, program: str, template_path: str, values_path: str) -> list:
        """The argv to run. Implemented per engine."""
        raise NotImplementedError

    def render(self, **extras):
        """Marshal the context to JSON, run the renderer, return its stdout."""
        program = self.executable()
        data = jsonable(template_data(getattr(self, "_globals_", None), extras))
        suffix = (self.EXT if isinstance(self.EXT, str) else self.EXT[0]) or ""
        with tempfile.TemporaryDirectory(prefix="netboot-tpl-") as workdir:
            template_path = os.path.join(workdir, self.BASENAME + suffix)
            values_path = os.path.join(workdir, self.VALUES_NAME)
            with open(template_path, "w", encoding="utf-8", newline="") as handle:
                handle.write(self._source)
            with open(values_path, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(data, handle)
            argv = self.command(program, template_path, values_path)
            try:
                result = subprocess.run(
                    argv,
                    capture_output=True,
                    timeout=self.TIMEOUT,
                )
            except subprocess.TimeoutExpired as exc:
                raise TemplateEngineError(
                    f"{self.COMMAND} did not finish rendering within "
                    f"{self.TIMEOUT}s"
                ) from exc
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace").strip()
            raise TemplateEngineError(
                f"{self.COMMAND} failed to render the template "
                f"(exit {result.returncode}): {stderr}"
            )
        # Decoded, not handed back as bytes: these are text templates, and the
        # programs emit UTF-8.
        return result.stdout.decode("utf-8")

    @classmethod
    def can_process(cls, file: Path, template: str) -> bool:
        """True for this engine's suffixes (`cls.EXT`)."""
        return can_process_suffix(cls, file)


#: Marshals the values file into `@context` and, for every top-level name that is
#: a legal Ruby instance variable, into `@name` as well -- so a template can say
#: `<%= @target['hostname'] %>` or `<%= @context['ctx']['target']['hostname'] %>`.
#: `trim_mode: '-'` makes `<%- -%>` available; `print` rather than `puts` so the
#: artifact's trailing newline is the template's own.
_ERB_SCRIPT = """
require 'erb'
require 'json'
@context = JSON.parse(File.read(ARGV[0]))
@context.each do |key, value|
  instance_variable_set("@#{key}", value) if key =~ /\\A[a-z_][A-Za-z0-9_]*\\z/
end
print ERB.new(File.read(ARGV[1]), trim_mode: '-').result(binding)
"""


class ERBTemplate(SubprocessTemplate):
    """An ERB template (`.erb`), rendered by the system `ruby`.

    Needs **ruby installed** (no Python package will do); `PIXIE_RUBY` names the
    interpreter if it is not on `PATH`.

    The context arrives as JSON: `@context` is the whole hash, and each top-level
    name is also set as an instance variable, so both of these work:

    ```erb
    Hello <%= @target['hostname'] %>, booting <%= @context['image']['_id'] %>
    <% @dhcpzone['nameservers'].each do |ns| %>nameserver <%= ns %>
    <% end %>
    ```

    Keys are strings and values are JSON scalars, lists or hashes -- an address
    or a path reaches the template as the string a template would have printed.
    `templates_undefined` does not apply: missing data is Ruby's `nil`, and what
    that renders as is the template's business.
    """

    EXT = (".erb",)
    COMMAND = "ruby"
    ENV_VAR = "PIXIE_RUBY"

    def command(self, program: str, template_path: str, values_path: str) -> list:
        return [program, "-e", _ERB_SCRIPT, values_path, template_path]


class EppTemplate(SubprocessTemplate):
    """A Puppet EPP template (`.epp`), rendered by `puppet epp render`.

    Needs **puppet installed**; `PIXIE_PUPPET` names it if it is not on `PATH`.

    The context is passed as a values file, so every top-level name is an EPP
    parameter:

    ```epp
    <%- | $target, $image | -%>
    kernel <%= $image['globals']['kernel'] %>
    append ip=<%= $target['ip'] %>
    ```

    A parameter list is optional -- `<%= $target['hostname'] %>` works without
    one. `templates_undefined` does not apply: an unknown name is Puppet's
    `undef`.
    """

    EXT = (".epp",)
    COMMAND = "puppet"
    ENV_VAR = "PIXIE_PUPPET"
    #: `puppet epp render` rejects any other extension for `--values_file`,
    #: whatever the file actually holds. JSON is valid YAML, so the contents are
    #: unchanged -- only the name is.
    VALUES_NAME = "values.yaml"

    def command(self, program: str, template_path: str, values_path: str) -> list:
        # `--values_file` reads YAML, and JSON is YAML -- so the same content
        # the ERB engine writes serves here (under a `.yaml` name, which puppet
        # insists on), with no Puppet-language serialiser needed.
        return [
            program,
            "epp",
            "render",
            "--values_file",
            values_path,
            template_path,
        ]
