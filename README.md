# ADM package registry

The public index of ADM libraries. Libraries are published by their authors from their own
repositories and installed with the `adm` toolchain; this repository is where `adm` looks
them up.

## Installing a library

Search, then install:

```
adm search json
adm get acme:jsonx
```

`acme` is the account that published the library and `jsonx` is its name. When only one
account publishes a name, `adm get jsonx` is enough; otherwise `adm` lists the candidates and
asks you to pick one. A version range narrows the choice: `adm get acme:jsonx@^1.2`.

Installing records the library in your project's `adm.toml` and `adm.lock`, so the next
`adm build` on any machine gets the same version. In code the library is used by its name:

```adm
use jsonx
```

`adm update` refreshes your local copy of this index; `adm get` and `adm search` do that on
their own unless you pass `--offline`.

## Publishing a library

1. **Get a signing key.** Run `adm app init` once in your project. It writes the manifest and
   creates your publisher key: the public half goes into the manifest, the private half into
   your config directory. Keep the private key safe and out of git; every package you publish
   is signed with it, and that signature is what lets people trust your updates.
2. **Name it.** The name is the module prefix people write after `use`, so pick one that will
   never collide: a fully qualified, reverse-domain name such as `com.acme.jsonx` rather than a
   bare word. 
3. **Declare the library.** Give the `library` block a doc comment (its first line becomes the
   description shown in search results), a version through `@manifest(version = "1.0.0")`, and
   an `export` list naming what users of the library may reach.
4. **Publish.** With a clean working tree and a git remote configured, run:

   ```
   adm publish
   ```

   This builds and signs the package, tags the release in your repository, and opens a pull
   request here. Checks run automatically and the pull request merges on its own when they
   pass; nothing waits on a person. A few minutes later `adm get` finds the new version.

Each publish needs a version newer than the last one. Prerelease versions such as
`2.0.0-beta.1` are fine and are only picked when asked for.

You publish under your own GitHub account or an organisation you belong to. The names `std`
and `builtin` are reserved.

## Hiding or removing a version

- **Yank** a version you regret with `adm yank jsonx@1.2.3`. Projects that already depend on
  it keep working; new installs skip it.
- **Remove** a version entirely, or a whole library, by opening an issue in this repository
  stating the account, name and versions. Removal breaks builds for anyone who depends on
  that version and has not vendored it, so it is done by hand and only when needed.

## Reporting a problem

If a package looks malicious, was published from a compromised account, or infringes on
something, open an issue here with the package name and version and what you found. A
package that clearly harms users is yanked first and discussed after.

## Private registries

A company can run its own registry from the same template and keep it private. List it under
`[registries]` in `adm.toml` and `adm get` resolves names from it first. Access works through
git, so whatever lets you read the repository already lets you install from it.
