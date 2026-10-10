/* Read-only ABI check: no device opens, GPIO requests or radio commands. */
#include <linux/gpio.h>
#include <linux/spi/spidev.h>
#include <stddef.h>
#include <stdio.h>

int main(void) {
    printf("{\"gpio_request_size\":%zu,\"gpio_config_size\":%zu,"
           "\"gpio_request_config_offset\":%zu,\"gpio_request_fd_offset\":%zu,"
           "\"spi_transfer_size\":%zu,"
           "\"gpio_get_chip\":%lu,\"gpio_get_lines\":%lu,"
           "\"gpio_get_values\":%lu,\"gpio_set_values\":%lu,"
           "\"spi_message\":%lu,\"spi_write_mode\":%lu,"
           "\"spi_write_bits\":%lu,\"spi_write_hz\":%lu}\n",
           sizeof(struct gpio_v2_line_request), sizeof(struct gpio_v2_line_config),
           offsetof(struct gpio_v2_line_request, config),
           offsetof(struct gpio_v2_line_request, fd), sizeof(struct spi_ioc_transfer),
           (unsigned long) GPIO_GET_CHIPINFO_IOCTL, (unsigned long) GPIO_V2_GET_LINE_IOCTL,
           (unsigned long) GPIO_V2_LINE_GET_VALUES_IOCTL, (unsigned long) GPIO_V2_LINE_SET_VALUES_IOCTL,
           (unsigned long) SPI_IOC_MESSAGE(1), (unsigned long) SPI_IOC_WR_MODE,
           (unsigned long) SPI_IOC_WR_BITS_PER_WORD, (unsigned long) SPI_IOC_WR_MAX_SPEED_HZ);
    return 0;
}
