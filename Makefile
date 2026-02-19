IMAGE := forgejo.bayleaf.wan.gothere.dev/tim/s33-modem-mon

.PHONY: push build

push: build
	docker push $(IMAGE)

build:
	docker build -t $(IMAGE) .