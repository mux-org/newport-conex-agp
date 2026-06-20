NAME = newport_conex_agp
FILENAME = $(NAME).tar
PLATFORM ?= linux/amd64

prod:
	podman build -f Dockerfile -t $(NAME) \
		--platform $(PLATFORM) \
		--no-cache .

dev:
	podman build -f Dockerfile -t $(NAME) \
		--target dev \
		--platform $(PLATFORM) \
		--no-cache .

save:
	podman save $(NAME) -o $(FILENAME)


.PHONY: prod dev save
.SILENT: prod dev save
