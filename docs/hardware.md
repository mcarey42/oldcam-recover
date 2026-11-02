# Getting the chip out and reading it

This is the only step that touches the camera, so take your time. If you'd rather
not do it yourself, any phone or electronics repair shop with BGA/SMD rework can
remove a TSOP48 chip for you; ask them to give you the chip, not to read it.

**Reading a NAND chip does not change what is stored on it.** What can lose your data
is erasing or writing it, so never press erase, program or "blank check and erase" in
the programmer software.

## 1. Open the camera and find the flash

- Take the batteries out first. These cameras were meant to be opened only by the
  store, so expect clips and screws under labels.
- The flash chip is a **TSOP48**: a thin rectangular chip about 12 x 20 mm, with 24
  legs on each long side. Note its markings, e.g. `HY27UA081G1M` (Hynix) or
  `K9F1G08U0A` (Samsung). Photograph the board and the chip's orientation (the dot
  or notch marks pin 1).

## 2. Remove the chip

- Protect nearby plastic parts and tiny components with **Kapton tape**. These boards
  are crowded.
- Apply plenty of **flux**, then a **low-melt alloy** (such as ChipQuik) along both
  rows of legs. It mixes with the factory solder and stays liquid at a much lower
  temperature, so the chip lifts with far less heat.
- If you have a preheater, warm the board from below to about 100-150 °C. Use hot air
  at roughly **300-330 °C**, keep the nozzle moving, and lift the chip with tweezers
  as soon as it floats free. Don't pry: torn pads are the usual way this goes wrong.
- Brief rework heat does not erase NAND. Avoid long soaks above about 250 °C at the
  chip body, but a normal removal is fine.

## 3. Clean and inspect

Wick the legs flat with braid and clean them with isopropyl alcohol. Check under a
magnifier that no legs are bent or bridged. Poor contact in the socket causes most
bad reads.

## 4. Read it

- In the programmer software, select your chip's exact part number (or a compatible
  128 MiB NAND with the same page size).
- Turn **off** any option that skips bad blocks, applies ECC, or removes the spare
  area. You want the raw chip, spare bytes included: the tool needs them.
- The file should be exactly **138,412,032 bytes** for these 128 MiB chips
  (65,536 pages of 2,112 bytes, or 262,144 of 528). If it is 134,217,728 bytes, the
  spare area was dropped: change the setting and read again.
- **Read it twice and compare** the two files' SHA-256 hashes
  (`sha256sum dump1.bin dump2.bin`, or `certutil -hashfile dump1.bin SHA256` on
  Windows). If they match, the read is good. If they don't, reseat the chip and read
  again.

## 5. Keep the dump safe

Copy the dump somewhere backed up before doing anything else. It is the only copy of
the data that doesn't depend on the chip. Then run `oldcam-recover recover` on it.

The chip can be soldered back afterwards if you want the camera whole, but the
camera won't let you download from it anyway, so most people keep the chip in an
anti-static bag with the dump.
