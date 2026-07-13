CURVER  := $(shell grep '^version' pyproject.toml | head -1 | sed 's/.*"\(.*\)"/\1/')
DIST    := dist

.PHONY: dist wheel checksums release tag clean

dist:
	git archive --format=tar.gz --prefix="Macaw-$(CURVER)/" HEAD \
		-o "$(DIST)/macaw-$(CURVER).tar.gz"

wheel:
	python -m build

checksums: dist wheel
	cd $(DIST) && sha256sum * > SHA256SUMS

release: checksums
	@echo "Release artifacts in $(DIST)/"
	@ls -lh $(DIST)/

# Cut a release: bump the version in pyproject.toml AND macaw-slint/Cargo.toml
# (+lock), commit, and tag. Pushing the tag triggers release.yml (Linux Slint
# bundles) + windows.yml (win64 Slint build + NSIS).
#   make tag VERSION=0.7.0 && git push && git push origin vVERSION
tag:
	@test -n "$(VERSION)" || { echo "usage: make tag VERSION=x.y.z"; exit 1; }
	@sed -i 's/^version = ".*"/version = "$(VERSION)"/' pyproject.toml
	@sed -i '0,/^version = ".*"/s//version = "$(VERSION)"/' macaw-slint/Cargo.toml
	@cd macaw-slint && cargo update -p macaw-ui --offline -q
	git add pyproject.toml macaw-slint/Cargo.toml macaw-slint/Cargo.lock
	git commit -m "Release v$(VERSION)"
	git tag -a "v$(VERSION)" -m "v$(VERSION)"
	@echo "Tagged v$(VERSION). Push:  git push && git push origin v$(VERSION)"

clean:
	rm -rf $(DIST) build *.egg-info src/*.egg-info
