import board
import busio
from PIL import Image, ImageDraw, ImageFont
import adafruit_ssd1306

# I2C setup
import busio
import board

i2c = busio.I2C(board.SCL, board.SDA, frequency=100000)

WIDTH = 128
HEIGHT = 64

oled = adafruit_ssd1306.SSD1306_I2C(WIDTH, HEIGHT, i2c, addr=0x3C)

oled.fill(0)
oled.show()

# Create image buffer
image = Image.new("1", (WIDTH, HEIGHT))
draw = ImageDraw.Draw(image)

# Draw border
draw.rectangle((0, 0, WIDTH-1, HEIGHT-1), outline=255)

# Draw text
draw.text((15, 20), "OLED WORKING", fill=255)

oled.image(image)
oled.show()
