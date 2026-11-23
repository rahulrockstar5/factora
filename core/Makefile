CC      ?= gcc
CFLAGS  ?= -O2 -fPIC -Wall -Wextra -fno-stack-protector -D_FORTIFY_SOURCE=0
OUT      = libfactora.so

all: $(OUT)

$(OUT): factora_core.c factora_core.h
	$(CC) $(CFLAGS) -shared -o $(OUT) factora_core.c

clean:
	rm -f $(OUT)

.PHONY: all clean
