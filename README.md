# dragon-os-ui-py

**DragonOS UI** — Alpine Linux uchun max-darajada chiroyli va tez GTK3 desktop interfeysi.

## Umumiy ko'rinish
Zamonaviy "glass" (shisha) dizayn ikki qismdan iborat:

- **Login (greeter)** — gradient fonda, monogram avatar, inline xato indikatorlari, PBKDF2 JSON foydalanuvchi bazasi.
- **Desktop** — glass top-bar (soat + batareya/tarmoq), stilkanay dock (magnify animatsiyasi), Launchpad, Spotlight, parallax wallpaper, silliq animatsiyalar.

## O'rnatish (Alpine Linux)

```sh
doas sh install-alpine.sh
```

O'rnatilgandan keyin:

```sh
dragon-os
```

Yoki qo'lda:

```sh
python3 main.py
```

## Kirish (login)

| Foydalanuvchi | Parol  |
|---------------|--------|
| `admin`       | `1234` |
| `guest`       | (bo'sh)|

Parolni `~/.config/dragonos/users.json` faylida PBKDF2 hash bilan o'zgartirish mumkin.

## Tugmalar (klaviatura)

| Qisqich         | Ish                                 |
|-----------------|-------------------------------------|
| `Super+L`       | Launchpad (ilova to'ri)             |
| `Super+Space`   | Spotlight (global qidiruv)          |
| `Ctrl+Alt+T`    | Terminal                            |
| `Super+G`       | Ikonkalar grid-rejimini almashtirish|
| `Double-click`  | Desktop fayllarini ochish           |
| `Right-click`   | Kontekst menyusi                    |

## Ilovalar
Tayyor: Terminal, Files, Notepad, Gallery, MusicPlayer, Calculator, Settings.

## Funksiyalar
- Batareya va tarmoq statusi sysfs-dan (Alpine-native, qo'shimcha paket talab etilmaydi)
- Dock magnify animatsiyasi + ochiq ilovani bilistarlik ko'rsatgichlari
- Spotlight: ilovaning va desktop fayllarining global qidiruvi
- Wallpaper parallax; har qanday PNG-ni bir qadamda fon qilish
- Alpine-mos: doas/sudo/pkexec -- poweroff/reboot bilan ishlaydi.

## Muallif
ABDULHODIY1 / DragonOS
