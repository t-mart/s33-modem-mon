IMAGE := docker.io/tmmrtn/s33-modem-mon

.PHONY: push build

push: build
	docker push $(IMAGE)

build:
	docker build -t $(IMAGE) .