# `isabelle scala_build`

Builds all Isabelle/Scala and Isabelle/Java modules registered by Isabelle components.

## Quick start

```bash
isabelle scala_build          # Incremental: rebuild only stale components
isabelle scala_build -f       # Force fresh rebuild of all components
isabelle scala_build -q       # Quiet: suppress compiler output
```

The command is typically unnecessary — certain Isabelle tools (`isabelle scala`, `isabelle scalac`, `isabelle console`; also `isabelle components -u/-x`) invoke `scala_build` automatically to ensure the classpath is up-to-date. Use it explicitly for testing, debugging, or after editing sources.

**Path convention**: all relative file paths in this document resolve from `$ISABELLE_HOME` (`/home/ws/apps/Isabelle/Isabelle2025/`).

## What it builds

The main output is `$ISABELLE_HOME/lib/classes/isabelle.jar` (~10 MB, ~340 Scala/Java sources). Additional component JARs (e.g., `isabelle_graphbrowser.jar` from `src/Tools/GraphBrowser/etc/build.props`) are built if their respective components register an `etc/build.props`.

## How it works

### Phase 1 — Discovery

`isabelle scala_build` is a bash script at [`lib/Tools/scala_build`](lib/Tools/scala_build). It invokes `isabelle.setup.Setup` with the `build` (or `build_fresh`) command.

[`Setup.java`](src/Tools/Setup/src/Setup.java) dispatches the subcommand: `build` (incremental), `build_fresh` (forced), `classpath` (print classpath and exit), or `services` (list registered service providers). `build` and `build_fresh` call [`Build.build_components()`](src/Tools/Setup/src/Build.java) which:

1. Reads `ISABELLE_COMPONENTS` (environment variable: colon-separated list of registered Isabelle component directories)
2. For each directory, checks for `etc/build.props` (the component's Scala/Java module declaration)
3. Creates a `Build.Context` per component, parsing these properties:
   - `title` — human-readable description
   - `module` — output JAR path
   - `no_build` — if `true`, skip this module (already provided externally)
   - `sources` — whitespace-separated list of `.scala` and `.java` files
   - `requirements` — dependency JARs needed at compile time (supports `env:VAR` to reference classpath variable)
   - `resources` — files copied into the JAR (`source:target` syntax)
   - `services` — service provider class names written to `META-INF/isabelle/services`
   - `scalac_options` / `javac_options` — extra compiler flags
   - `main` — main class for `java -jar`

### Phase 2 — Incrementality check

For each component, [`Build.build()`](src/Tools/Setup/src/Build.java) computes a SHA1 digest over:

1. The `build.props` metadata itself (all properties, sorted)
2. Each requirement file's content
3. Each resource file's content
4. Each source file's content

This digest is compared against the stored digest inside the existing JAR at `META-INF/isabelle/shasum`. If they match (and `-f` was not passed), the component is **skipped** — no recompilation.

If the component has no sources, resources, or services (`is_vacuous`), the JAR is simply deleted.

### Phase 3 — Compilation

If the digest changed (or `-f` forced), compilation proceeds in a temporary directory:

1. **Scala compilation** — The Dotty compiler (`dotty.tools.dotc.Driver`) compiles all `.scala` sources. Options come from the environment variable `ISABELLE_SCALAC_OPTIONS` (set by the Isabelle settings system) plus component-specific `scalac_options`. Flags: `-d <build_dir> -bootclasspath <deps>`. If there are no `.scala` files, this step is skipped.

2. **Java compilation** — `javax.tools.JavaCompiler` compiles all `.java` sources. The previously compiled Scala classes are added to the classpath so Java can reference Scala types. Options come from `ISABELLE_JAVAC_OPTIONS` plus component-specific `javac_options`. If there are no `.java` files, this step is skipped.

Both compilers write output to the same temporary `build_dir`.

### Phase 4 — Packaging

1. **Resources** — Resource files are copied into `build_dir` according to their `source:target` mappings. A target ending in `/` means the file is placed inside that directory; otherwise it's renamed.

2. **SHA1 digest** — The computed SHA1 is written to `META-INF/isabelle/shasum` inside the build directory (for future incrementality checks).

3. **Services** — Service class names are written to `META-INF/isabelle/services`, one per line. This is the Isabelle service registry consumed by `Isabelle_System.make_services()` at runtime.

4. **JAR creation** — Everything under `build_dir` is packed into the output JAR with a standard manifest (`MANIFEST.MF` version, `Created-By`, and optional `Main-Class`).

5. **Cleanup** — The temporary `build_dir` is recursively deleted.

## Key files

| File | Role |
|------|------|
| [`lib/Tools/scala_build`](lib/Tools/scala_build) | Bash entry point; parses `-f`/`-q`, launches Java |
| [`src/Tools/Setup/src/Setup.java`](src/Tools/Setup/src/Setup.java) | Java entry point; dispatches `build`/`build_fresh`/`classpath`/`services` |
| [`src/Tools/Setup/src/Build.java`](src/Tools/Setup/src/Build.java) | Core build engine: discovery, SHA1 incrementality, Scala+Java compilation, JAR packaging |
| [`etc/build.props`](etc/build.props) | Main Isabelle component config: ~340 sources → `isabelle.jar` |
| [`src/Pure/Tools/scala_build.scala`](src/Pure/Tools/scala_build.scala) | Scala wrapper (`Scala_Build`) used by other Scala tools and ML code (not the CLI) |

## Relationship to Gradle/IDE projects

`isabelle scala_build` is the **production** build system. The Gradle project in this workspace is solely for IDE support (code navigation, type checking) and was generated by `isabelle scala_project -G -L`. Do not use Gradle to produce JARs — the build.props mechanism manages dependency ordering and the `META-INF/isabelle/shasum` integration that Gradle knows nothing about.

See the [System Manual](doc/system.pdf) §2 for the formal specification of `scala_build`, `scala_project`, and `build.props`.
